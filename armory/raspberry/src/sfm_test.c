#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <termios.h>
#include <poll.h>
#include <ctype.h>

#include "UF_API.h"

#define DEFAULT_PORT "/dev/ttyACM0"
#define DEFAULT_BAUD 115200
#define TIMEOUT_IDENTIFY_POLL 0x08 /* 짧게: 1 누를 때 전환 대기 단축 */
#define TIMEOUT_ENROLL        0x3C /* ~6s */

enum {
    CMD_NONE = 0,
    CMD_ENROLL,
    CMD_DELETE,
    CMD_DELETE_ALL,
    CMD_STATUS,
    CMD_HELP,
    CMD_QUIT,
};

static char g_port_buf[64] = DEFAULT_PORT;
static const char *g_port = g_port_buf;
static UINT32 g_saved_timeout = TIMEOUT_IDENTIFY_POLL;
static int g_connected = 0;
static int g_port_fixed = 0;

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_cmd = CMD_NONE;
static int g_running = 1;
static int g_input_paused = 0;
static volatile int g_enroll_collecting = 0;
static char g_pre_enroll_id[64];
static int g_pre_enroll_ready = 0;
static volatile int g_in_identify = 0;
static volatile int g_sdk_busy = 0; /* open/close/reopen 중 Cancel 금지 */

static struct termios g_term_orig;
static int g_term_raw = 0;

static const char *ret_str(UF_RET_CODE ret)
{
    switch (ret) {
    case UF_RET_SUCCESS: return "SUCCESS";
    case UF_ERR_CANNOT_OPEN_SERIAL: return "CANNOT_OPEN_SERIAL";
    case UF_ERR_CANNOT_SETUP_SERIAL: return "CANNOT_SETUP_SERIAL";
    case UF_ERR_CANNOT_WRITE_SERIAL: return "CANNOT_WRITE_SERIAL";
    case UF_ERR_WRITE_SERIAL_TIMEOUT: return "WRITE_TIMEOUT";
    case UF_ERR_CANNOT_READ_SERIAL: return "CANNOT_READ_SERIAL";
    case UF_ERR_READ_SERIAL_TIMEOUT: return "READ_TIMEOUT";
    case UF_ERR_CHECKSUM_ERROR: return "CHECKSUM_ERROR";
    case UF_ERR_CANNOT_SET_TIMEOUT: return "CANNOT_SET_TIMEOUT";
    case UF_ERR_COMMAND_FAILED: return "COMMAND_FAILED";
    case UF_ERR_SCAN_FAIL: return "SCAN_FAIL";
    case UF_ERR_NOT_FOUND: return "NOT_FOUND";
    case UF_ERR_NOT_MATCH: return "NOT_MATCH";
    case UF_ERR_TRY_AGAIN: return "TRY_AGAIN";
    case UF_ERR_TIME_OUT: return "TIME_OUT";
    case UF_ERR_MEM_FULL: return "MEM_FULL";
    case UF_ERR_EXIST_ID: return "EXIST_ID";
    case UF_ERR_FINGER_LIMIT: return "FINGER_LIMIT";
    case UF_ERR_INVALID_ID: return "INVALID_ID";
    case UF_ERR_EXIST_FINGER: return "EXIST_FINGER";
    case UF_ERR_TIMEOUT_MATCH: return "TIMEOUT_MATCH";
    case UF_ERR_CANCELED: return "CANCELED";
    case UF_ERR_BUSY: return "BUSY";
    case UF_ERR_FAKE_DETECTED: return "FAKE_DETECTED";
    default: return "UNKNOWN";
    }
}

static void on_enroll(BYTE errCode, UF_ENROLL_MODE enrollMode, int numOfSuccess)
{
    (void)enrollMode;
    if (errCode == UF_PROTO_RET_SCAN_SUCCESS) {
        printf("  [스캔 성공] %d회 완료. 같은 손가락을 다시 올려주세요.\n", numOfSuccess);
    } else if (errCode == UF_PROTO_RET_CONTINUE || errCode == UF_PROTO_RET_TRY_AGAIN) {
        printf("  [계속] 손가락을 다시 올려주세요.\n");
    }
    fflush(stdout);
}

static void on_identify(BYTE code)
{
    if (code == UF_PROTO_RET_SCAN_SUCCESS) {
        printf("  [스캔됨] 확인 중...\n");
        fflush(stdout);
    }
}

static void term_set_raw(int enable)
{
    struct termios t;

    if (!isatty(STDIN_FILENO)) {
        return;
    }
    if (enable) {
        if (!g_term_raw) {
            tcgetattr(STDIN_FILENO, &g_term_orig);
        }
        t = g_term_orig;
        t.c_lflag &= ~(ICANON | ECHO);
        t.c_cc[VMIN] = 1;
        t.c_cc[VTIME] = 0;
        tcsetattr(STDIN_FILENO, TCSANOW, &t);
        g_term_raw = 1;
    } else if (g_term_raw) {
        tcsetattr(STDIN_FILENO, TCSANOW, &g_term_orig);
        g_term_raw = 0;
    }
}

static int peek_cmd(void)
{
    int cmd;
    pthread_mutex_lock(&g_lock);
    cmd = g_cmd;
    pthread_mutex_unlock(&g_lock);
    return cmd;
}

static void set_cmd(int cmd)
{
    int prev;

    pthread_mutex_lock(&g_lock);
    prev = g_cmd;
    if (prev == CMD_NONE) {
        g_cmd = cmd;
    }
    pthread_mutex_unlock(&g_lock);

    /* 이미 처리 대기 중이면 무시 (중복 키) */
    if (prev != CMD_NONE) {
        return;
    }

    /* 재연결 중이 아니고 Identify 중일 때만 Cancel.
     * FALSE: 응답 대기 없이 즉시 중단 시도 (전환 지연 감소) */
    if (g_connected && g_in_identify && !g_sdk_busy) {
        UF_Cancel(FALSE);
    }
}

static int take_cmd(void)
{
    int cmd;
    pthread_mutex_lock(&g_lock);
    cmd = g_cmd;
    g_cmd = CMD_NONE;
    pthread_mutex_unlock(&g_lock);
    return cmd;
}

static void pause_input(int paused)
{
    pthread_mutex_lock(&g_lock);
    g_input_paused = paused;
    pthread_mutex_unlock(&g_lock);
}

static int is_input_paused(void)
{
    int paused;
    pthread_mutex_lock(&g_lock);
    paused = g_input_paused;
    pthread_mutex_unlock(&g_lock);
    return paused;
}

static void close_module(void)
{
    if (g_connected) {
        UF_CloseCommPort();
        g_connected = 0;
    }
}

static int wait_for_port(int retries)
{
    int i;
    for (i = 0; i < retries; i++) {
        if (access(g_port, F_OK) == 0) {
            return 0;
        }
        usleep(200000);
    }
    return -1;
}

static int discover_acm_port(void)
{
    int i;
    char path[64];
    char found[64];
    int have = 0;

    if (g_port_fixed) {
        return (access(g_port, F_OK) == 0) ? 0 : -1;
    }

    found[0] = '\0';
    for (i = 0; i < 10; i++) {
        snprintf(path, sizeof(path), "/dev/ttyACM%d", i);
        if (access(path, F_OK) == 0) {
            strncpy(found, path, sizeof(found) - 1);
            found[sizeof(found) - 1] = '\0';
            have = 1;
        }
    }
    if (!have) {
        return -1;
    }
    if (strcmp(g_port_buf, found) != 0) {
        printf("(시리얼 포트 변경: %s -> %s)\n", g_port_buf, found);
        fflush(stdout);
        strncpy(g_port_buf, found, sizeof(g_port_buf) - 1);
        g_port_buf[sizeof(g_port_buf) - 1] = '\0';
    }
    return 0;
}

static int open_module(void)
{
    UF_RET_CODE ret;
    UINT32 module_id = 0;

    g_sdk_busy = 1;
    close_module();
    g_sdk_busy = 1;

    if (discover_acm_port() != 0) {
        usleep(300000);
        if (discover_acm_port() != 0) {
            printf("포트 장치 없음 (ttyACM*). USB를 확인하세요.\n");
            g_sdk_busy = 0;
            return -1;
        }
    }
    if (wait_for_port(10) != 0) {
        printf("포트 장치 없음: %s\n", g_port);
        g_sdk_busy = 0;
        return -1;
    }
    usleep(50000);

    ret = UF_InitCommPort(g_port, DEFAULT_BAUD, FALSE);
    if (ret != UF_RET_SUCCESS) {
        printf("포트 열기 실패: %s (%d) [%s]\n", g_port, (int)ret, ret_str(ret));
        g_sdk_busy = 0;
        return -1;
    }
    g_connected = 1;

    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
    UF_SetEnrollCallback(on_enroll);
    UF_SetIdentifyCallback(on_identify);

    ret = UF_SearchModuleID(&module_id);
    if (ret != UF_RET_SUCCESS) {
        printf("모듈 검색 실패(%s). 재오픈 재시도...\n", ret_str(ret));
        UF_CloseCommPort();
        g_connected = 0;
        sleep(2); /* 모듈 BUSY/재열거 대기 */
        if (discover_acm_port() != 0 || wait_for_port(15) != 0) {
            g_sdk_busy = 0;
            return -1;
        }
        usleep(300000);
        ret = UF_InitCommPort(g_port, DEFAULT_BAUD, FALSE);
        if (ret != UF_RET_SUCCESS) {
            g_sdk_busy = 0;
            return -1;
        }
        g_connected = 1;
        UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
        UF_SetEnrollCallback(on_enroll);
        UF_SetIdentifyCallback(on_identify);
        ret = UF_SearchModuleID(&module_id);
    }

    if (ret != UF_RET_SUCCESS) {
        printf("모듈 검색 실패: %d (%s)\n", (int)ret, ret_str(ret));
        UF_CloseCommPort();
        g_connected = 0;
        g_sdk_busy = 0;
        return -1;
    }

    if (UF_GetSysParameter(UF_SYS_TIMEOUT, &g_saved_timeout) != UF_RET_SUCCESS) {
        g_saved_timeout = TIMEOUT_ENROLL;
    }
    UF_SetSysParameter(UF_SYS_TIMEOUT, TIMEOUT_IDENTIFY_POLL);

    printf("연결됨: %s  module_id=%u\n", g_port, module_id);
    g_sdk_busy = 0;
    return 0;
}

static int reopen_clean(void)
{
    static int fail_streak = 0;
    int ok;

    printf("(모듈 연결을 다시 준비합니다...)\n");
    fflush(stdout);
    ok = open_module();
    if (ok != 0) {
        fail_streak++;
        /* NOT_FOUND 스팸 방지: 실패할수록 더 길게 대기 */
        sleep(fail_streak < 3 ? 2 : 4);
        return -1;
    }
    fail_streak = 0;
    return 0;
}

static int g_comm_soft_fail = 0;

/* 스캔 직후 모듈이 바쁠 때 짧은 안정화 */
static void settle_after_scan(void)
{
    usleep(1000000); /* 1초: 손가락 뗌 + BUSY 해소 */
}

static void wait_identify_once(void)
{
    UINT32 user_id = 0;
    BYTE sub_id = 0;
    UF_RET_CODE ret;

    if (!g_connected) {
        if (peek_cmd() != CMD_NONE || g_enroll_collecting) {
            return;
        }
        reopen_clean();
        return;
    }

    g_in_identify = 1;
    ret = UF_Identify(&user_id, &sub_id);
    g_in_identify = 0;

    if (peek_cmd() != CMD_NONE || g_enroll_collecting) {
        return;
    }

    if (ret == UF_RET_SUCCESS) {
        g_comm_soft_fail = 0;
        printf(">>> 지문 확인됨 (등록된 지문)  user_id=%u  sub_id=%u\n",
               user_id, (unsigned)sub_id);
        fflush(stdout);
        settle_after_scan();
        return;
    }
    if (ret == UF_ERR_NOT_FOUND || ret == UF_ERR_NOT_MATCH) {
        g_comm_soft_fail = 0;
        printf(">>> 등록되지 않은 지문입니다.\n");
        fflush(stdout);
        settle_after_scan();
        return;
    }
    if (ret == UF_ERR_TIME_OUT || ret == UF_ERR_TIMEOUT_MATCH ||
        ret == UF_ERR_CANCELED) {
        g_comm_soft_fail = 0;
        return;
    }
    if (ret == UF_ERR_SCAN_FAIL) {
        g_comm_soft_fail = 0;
        printf(">>> 스캔 실패. 다시 올려주세요.\n");
        fflush(stdout);
        usleep(400000);
        return;
    }

    /* BUSY: 스캔 직후 흔함. 재연결하지 말고 잠깐만 쉼 */
    if (ret == UF_ERR_BUSY) {
        usleep(800000);
        return;
    }

    if (ret == UF_ERR_CANNOT_READ_SERIAL ||
        ret == UF_ERR_CANNOT_WRITE_SERIAL ||
        ret == UF_ERR_READ_SERIAL_TIMEOUT ||
        ret == UF_ERR_WRITE_SERIAL_TIMEOUT ||
        ret == UF_ERR_CHECKSUM_ERROR ||
        ret == UF_ERR_COMMAND_FAILED) {
        g_comm_soft_fail++;
        if (g_comm_soft_fail < 5) {
            usleep(600000);
            return;
        }
        g_comm_soft_fail = 0;
        printf("통신 불안정(%s). 재연결합니다...\n", ret_str(ret));
        fflush(stdout);
        reopen_clean();
        return;
    }

    printf(">>> 확인 오류: %d (%s)\n", (int)ret, ret_str(ret));
    fflush(stdout);
    sleep(1);
}

static void print_status(void)
{
    UINT32 num = 0, maxn = 0, enrolled = 0;
    UINT32 num_user = 0, num_tpl = 0;
    UFUserInfo users[512];
    UF_RET_CODE r1, r2, r3;

    if (!g_connected) {
        printf("연결 안 됨\n");
        return;
    }
    r1 = UF_GetNumOfTemplate(&num);
    r2 = UF_GetMaxNumOfTemplate(&maxn);
    UF_ReadSysParameter(UF_SYS_ENROLLED_FINGER, &enrolled);
    r3 = UF_GetAllUserInfo(users, &num_user, &num_tpl);

    printf("등록 현황: 사용자 %u명 / 템플릿 %u개", num_user, num_tpl);
    if (r3 != UF_RET_SUCCESS) {
        printf(" (GetAllUserInfo 실패:%d)", (int)r3);
    }
    printf("\n");
    fflush(stdout);
    (void)r1;
    (void)r2;
    (void)num;
    (void)maxn;
    (void)enrolled;
}

static UINT32 get_template_count(void)
{
    UFUserInfo users[512];
    UINT32 num_user = 0, num_tpl = 0;
    UINT32 num = 0;

    if (UF_GetAllUserInfo(users, &num_user, &num_tpl) == UF_RET_SUCCESS) {
        return num_tpl;
    }
    /* fallback */
    if (UF_GetNumOfTemplate(&num) != UF_RET_SUCCESS) {
        return 0xffffffffu;
    }
    return num;
}

static int clear_all_templates(void)
{
    UF_RET_CODE ret;
    UINT32 before, after;
    UFUserInfo users[512];
    UINT32 num_user = 0, num_tpl = 0;
    UINT32 i;
    int deleted = 0;

    before = get_template_count();
    printf("삭제 전: 템플릿 %u개\n", before);
    fflush(stdout);

    /* 1) 사용자 목록 기준 개별 삭제 (가장 확실) */
    ret = UF_GetAllUserInfo(users, &num_user, &num_tpl);
    if (ret == UF_RET_SUCCESS && num_user > 0) {
        printf("사용자 %u명 개별 삭제 중...\n", num_user);
        for (i = 0; i < num_user; i++) {
            ret = UF_Delete(users[i].userID);
            printf("  UF_Delete(id=%u) -> %d (%s)\n",
                   users[i].userID, (int)ret, ret_str(ret));
        }
        usleep(200000);
    }

    /* 2) DeleteAll */
    ret = UF_DeleteAll();
    printf("UF_DeleteAll -> %d (%s)\n", (int)ret, ret_str(ret));
    usleep(200000);

    after = get_template_count();
    printf("삭제 후: 템플릿 %u개\n", after);
    fflush(stdout);
    if (after == 0) {
        printf("전체 삭제 완료.\n");
        return 0;
    }

    /* 3) 범위 삭제 재시도 */
    printf("잔여 있음 → DeleteMultipleTemplates 재시도...\n");
    deleted = 0;
    ret = UF_DeleteMultipleTemplates(1, 30000, &deleted);
    printf("UF_DeleteMultipleTemplates -> %d, deletedIDs=%d\n", (int)ret, deleted);
    usleep(200000);
    after = get_template_count();
    printf("재시도 후: 템플릿 %u개\n", after);
    fflush(stdout);

    if (after == 0) {
        printf("전체 삭제 완료.\n");
        return 0;
    }

    printf("경고: 모듈에 템플릿/사용자 정보가 남아 있습니다. (삭제 미완료)\n");
    return -1;
}

static void print_help(void)
{
    printf("\n-------- 명령 (숫자만 누르면 즉시 반응) --------\n");
    printf(" 1  등록 (Enroll)\n");
    printf(" 4  삭제 (Delete by ID)\n");
    printf(" 5  전체 삭제 (Delete All)\n");
    printf(" 6  상태 보기\n");
    printf(" h  도움말\n");
    printf(" 0  종료\n");
    printf("------------------------------------------------\n");
    printf("기본: 지문 확인 대기 / 1 누르면 등록 모드\n\n");
    fflush(stdout);
}

static void trim_inplace(char *s)
{
    char *p = s;
    size_t n;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') {
        p++;
    }
    if (p != s) {
        memmove(s, p, strlen(p) + 1);
    }
    n = strlen(s);
    while (n > 0 && (s[n - 1] == ' ' || s[n - 1] == '\t' ||
                      s[n - 1] == '\r' || s[n - 1] == '\n')) {
        s[--n] = '\0';
    }
}

static void *input_thread(void *arg)
{
    (void)arg;
    while (g_running) {
        unsigned char ch;
        ssize_t n;
        struct pollfd pfd;

        if (is_input_paused() || g_enroll_collecting) {
            usleep(30000);
            continue;
        }

        pfd.fd = STDIN_FILENO;
        pfd.events = POLLIN;
        if (poll(&pfd, 1, 100) <= 0) {
            continue;
        }
        if (is_input_paused() || g_enroll_collecting) {
            continue;
        }
        if (!(pfd.revents & POLLIN)) {
            continue;
        }

        n = read(STDIN_FILENO, &ch, 1);
        if (n <= 0) {
            if (!g_running) {
                break;
            }
            continue;
        }
        if (is_input_paused() || g_enroll_collecting) {
            continue;
        }

        switch (ch) {
        case '1':
            /* ID 입력을 즉시 받아, Identify 종료를 기다리지 않음 */
            printf("\n>>> 등록 모드로 전환합니다.\n");
            printf("[등록] 사용자 ID 입력 (0=자동발급): ");
            fflush(stdout);

            g_enroll_collecting = 1;
            if (g_connected && g_in_identify && !g_sdk_busy) {
                UF_Cancel(FALSE);
            }

            term_set_raw(0);
            tcflush(STDIN_FILENO, TCIFLUSH);
            g_pre_enroll_ready = 0;
            if (fgets(g_pre_enroll_id, (int)sizeof(g_pre_enroll_id), stdin)) {
                trim_inplace(g_pre_enroll_id);
                g_pre_enroll_ready = 1;
            }
            term_set_raw(1);

            set_cmd(CMD_ENROLL);
            g_enroll_collecting = 0;
            break;
        case '4':
            printf("\n>>> 삭제 모드로 전환합니다.\n");
            fflush(stdout);
            set_cmd(CMD_DELETE);
            break;
        case '5':
            printf("\n>>> 전체 삭제 모드로 전환합니다.\n");
            fflush(stdout);
            set_cmd(CMD_DELETE_ALL);
            break;
        case '6':
            set_cmd(CMD_STATUS);
            break;
        case 'h':
        case 'H':
        case '?':
            set_cmd(CMD_HELP);
            break;
        case '0':
            set_cmd(CMD_QUIT);
            break;
        default:
            break;
        }
    }
    return NULL;
}

static int read_line_main(char *buf, size_t buflen)
{
    pause_input(1);
    term_set_raw(0);
    /* input thread가 poll 루프에서 stdin을 놓을 때까지 짧게 대기 */
    usleep(60000);
    tcflush(STDIN_FILENO, TCIFLUSH);

    if (!fgets(buf, (int)buflen, stdin)) {
        term_set_raw(1);
        pause_input(0);
        return -1;
    }
    trim_inplace(buf);
    term_set_raw(1);
    pause_input(0);
    return 0;
}

/* Cancel 직후 안정화. force_reopen=1 이면 포트 재오픈 */
static int prepare_for_command(int force_reopen)
{
    usleep(50000);
    if (!force_reopen && g_connected) {
        return 0;
    }
    return reopen_clean();
}

static int enroll_once(UINT32 user_id, UF_ENROLL_OPTION option,
                       UINT32 *enroll_id, UINT32 *quality)
{
    UF_RET_CODE ret;

    UF_SetSysParameter(UF_SYS_TIMEOUT, TIMEOUT_ENROLL);
    ret = UF_Enroll(user_id, option, enroll_id, quality);
    UF_SetSysParameter(UF_SYS_TIMEOUT, TIMEOUT_IDENTIFY_POLL);
    return (int)ret;
}

static int ensure_module_ready(void)
{
    UINT32 mid = 0;
    int i;

    if (!g_connected) {
        return reopen_clean();
    }
    for (i = 0; i < 10; i++) {
        if (UF_SearchModuleID(&mid) == UF_RET_SUCCESS && mid != 0) {
            return 0;
        }
        usleep(300000);
    }
    printf("모듈이 바빠서 재연결합니다...\n");
    fflush(stdout);
    return reopen_clean();
}

static void do_enroll(void)
{
    char line[64];
    UINT32 user_id = 0;
    UINT32 enroll_id = 0;
    UINT32 quality = 0;
    UF_ENROLL_OPTION option;
    int ret;

    if (g_pre_enroll_ready) {
        strncpy(line, g_pre_enroll_id, sizeof(line) - 1);
        line[sizeof(line) - 1] = '\0';
        g_pre_enroll_ready = 0;
    } else {
        printf("\n[등록] 사용자 ID 입력 (0=자동발급): ");
        fflush(stdout);
        if (read_line_main(line, sizeof(line)) != 0) {
            return;
        }
    }

    user_id = (UINT32)strtoul(line, NULL, 10);
    option = (user_id == 0) ? UF_ENROLL_AUTO_ID : UF_ENROLL_ADD_NEW;

    /* Cancel/Identify 직후 BUSY 해소 대기 */
    usleep(500000);
    if (ensure_module_ready() != 0) {
        printf("등록 실패: 모듈 준비 불가\n");
        return;
    }

    if (user_id == 0) {
        printf(">> 자동 ID 등록. 센서 램프가 켜지면 손가락을 올려주세요.\n");
    } else {
        printf(">> ID=%u 등록. 센서 램프가 켜지면 손가락을 올려주세요.\n", user_id);
    }
    fflush(stdout);

    ret = enroll_once(user_id, option, &enroll_id, &quality);

    if (ret != UF_RET_SUCCESS &&
        (ret == UF_ERR_CHECKSUM_ERROR || ret == UF_ERR_BUSY ||
         ret == UF_ERR_CANNOT_WRITE_SERIAL || ret == UF_ERR_CANNOT_READ_SERIAL ||
         ret == UF_ERR_COMMAND_FAILED || ret == UF_ERR_CANCELED ||
         ret == UF_ERR_READ_SERIAL_TIMEOUT)) {
        printf("등록 통신 오류(%s). 재연결 후 다시 시도합니다. 손가락을 올려주세요.\n",
               ret_str((UF_RET_CODE)ret));
        fflush(stdout);
        if (reopen_clean() != 0) {
            return;
        }
        ret = enroll_once(user_id, option, &enroll_id, &quality);
    }

    if (ret == UF_RET_SUCCESS) {
        printf("등록 완료: enroll_id=%u, quality=%u\n", enroll_id, quality);
    } else {
        printf("등록 실패: %d (%s)\n", ret, ret_str((UF_RET_CODE)ret));
    }
    print_status();
}

static void do_delete_one(void)
{
    char line[64];
    UINT32 user_id = 0;
    UF_RET_CODE ret;

    printf("\n[삭제] 사용자 ID: ");
    fflush(stdout);
    if (read_line_main(line, sizeof(line)) != 0) {
        return;
    }
    user_id = (UINT32)strtoul(line, NULL, 10);
    if (user_id == 0) {
        printf("유효한 ID를 입력하세요.\n");
        return;
    }

    if (prepare_for_command(0) != 0) {
        return;
    }

    ret = UF_Delete(user_id);
    if (ret != UF_RET_SUCCESS) {
        printf("삭제 실패(%s), 재연결 후 재시도...\n", ret_str(ret));
        if (prepare_for_command(1) != 0) {
            return;
        }
        ret = UF_Delete(user_id);
    }
    printf("삭제 결과: %d (%s)  id=%u\n", (int)ret, ret_str(ret), user_id);
    print_status();
}

static int is_yes(const char *s)
{
    return (strcmp(s, "yes") == 0 || strcmp(s, "YES") == 0 || strcmp(s, "Yes") == 0 ||
            strcmp(s, "y") == 0 || strcmp(s, "Y") == 0);
}

static void do_delete_all(void)
{
    char line[64];

    printf("\n[전체 삭제] 진행하려면 yes 입력: ");
    fflush(stdout);
    if (read_line_main(line, sizeof(line)) != 0) {
        return;
    }
    if (!is_yes(line)) {
        printf("취소됨. (입력='%s')\n", line);
        return;
    }

    printf("전체 삭제 실행 중...\n");
    fflush(stdout);

    /* Cancel 직후 false SUCCESS 방지: 반드시 재연결 후 삭제·검증 */
    if (prepare_for_command(1) != 0) {
        return;
    }

    if (clear_all_templates() != 0) {
        printf("재연결 후 한 번 더 시도...\n");
        if (prepare_for_command(1) == 0) {
            clear_all_templates();
        }
    }
    print_status();
}

int main(int argc, char **argv)
{
    pthread_t tid;
    int major = 0, minor = 0, revision = 0;
    int first_wait_msg = 1;
    int cmd;

    if (argc >= 2) {
        strncpy(g_port_buf, argv[1], sizeof(g_port_buf) - 1);
        g_port_buf[sizeof(g_port_buf) - 1] = '\0';
        g_port_fixed = 1;
    } else {
        discover_acm_port();
    }

    UF_GetSDKVersion(&major, &minor, &revision);
    printf("SFM SDK %d.%d.%d\n", major, minor, revision);

    if (open_module() != 0) {
        return 1;
    }
    print_status();
    print_help();

    term_set_raw(1);
    if (pthread_create(&tid, NULL, input_thread, NULL) != 0) {
        perror("pthread_create");
        term_set_raw(0);
        close_module();
        return 1;
    }

    while (g_running) {
        cmd = take_cmd();
        if (cmd != CMD_NONE) {
            first_wait_msg = 1;
            /* Identify/Cancel이 완전히 끝난 뒤 명령 실행 */
            while (g_in_identify) {
                usleep(10000);
            }
            usleep(30000);

            switch (cmd) {
            case CMD_QUIT:
                g_running = 0;
                break;
            case CMD_ENROLL:
                do_enroll();
                break;
            case CMD_DELETE:
                do_delete_one();
                break;
            case CMD_DELETE_ALL:
                do_delete_all();
                break;
            case CMD_STATUS:
                if (!g_connected) {
                    reopen_clean();
                }
                print_status();
                break;
            case CMD_HELP:
                print_help();
                break;
            default:
                break;
            }
            continue;
        }

        if (first_wait_msg) {
            printf("[확인 대기] 손가락을 올리거나, 1 을 눌러 등록하세요.\n");
            fflush(stdout);
            first_wait_msg = 0;
        }
        wait_identify_once();
    }

    printf("\n종료합니다.\n");
    g_running = 0;
    if (g_connected && !g_sdk_busy) {
        if (g_in_identify) {
            UF_Cancel(TRUE);
        }
        UF_SetSysParameter(UF_SYS_TIMEOUT, g_saved_timeout);
    }
    close_module();
    term_set_raw(0);
    pthread_join(tid, NULL);
    return 0;
}
