/*
 * sfm_daemon — always-on Identify wait + UI enroll control over Unix socket.
 * Protocol: newline-delimited JSON (one object per line).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>
#include <pthread.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <poll.h>
#include <ctype.h>
#include <signal.h>

#include "UF_API.h"

#define DEFAULT_PORT "/dev/ttyACM0"
#define DEFAULT_BAUD 115200
#define TIMEOUT_IDENTIFY_POLL 0x08
#define TIMEOUT_ENROLL        0x3C
#define MAX_CLIENTS 8
#define SOCK_PATH_DEFAULT "/home/pi/fingerprint/run/sfm.sock"
#define LINE_MAX 512

enum {
    CMD_NONE = 0,
    CMD_ENROLL,
    CMD_ABORT,
    CMD_DELETE,
    CMD_DELETE_ALL,
    CMD_STATUS,
    CMD_QUIT,
};

static char g_port_buf[64] = DEFAULT_PORT;
static const char *g_port = g_port_buf;
static UINT32 g_saved_timeout = TIMEOUT_IDENTIFY_POLL;
static int g_connected = 0;
static int g_port_fixed = 0;
static char g_sock_path[256] = SOCK_PATH_DEFAULT;

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t g_io_lock;
static pthread_once_t g_io_once = PTHREAD_ONCE_INIT;
static int g_cmd = CMD_NONE;
static UINT32 g_cmd_user_id = 1;
#define DELETE_Q_MAX 256
static UINT32 g_delete_q[DELETE_Q_MAX];
static int g_delete_q_head = 0;
static int g_delete_q_tail = 0;
static int g_running = 1;
static volatile int g_in_identify = 0;
static volatile int g_sdk_busy = 0;
static volatile int g_enroll_scans = 0;

static int g_listen_fd = -1;
static int g_clients[MAX_CLIENTS];
static int g_nclients = 0;
static int g_cached_users = -1;

static int count_users(void);
static void refresh_user_count(void);
static void emit_status(void);
static void emit_event(const char *fmt, ...);
static int peek_cmd(void);

static const char *ret_str(UF_RET_CODE ret)
{
    switch (ret) {
    case UF_RET_SUCCESS: return "SUCCESS";
    case UF_ERR_CANNOT_OPEN_SERIAL: return "CANNOT_OPEN_SERIAL";
    case UF_ERR_CANNOT_WRITE_SERIAL: return "CANNOT_WRITE_SERIAL";
    case UF_ERR_CANNOT_READ_SERIAL: return "CANNOT_READ_SERIAL";
    case UF_ERR_READ_SERIAL_TIMEOUT: return "READ_TIMEOUT";
    case UF_ERR_WRITE_SERIAL_TIMEOUT: return "WRITE_TIMEOUT";
    case UF_ERR_CHECKSUM_ERROR: return "CHECKSUM_ERROR";
    case UF_ERR_COMMAND_FAILED: return "COMMAND_FAILED";
    case UF_ERR_SCAN_FAIL: return "SCAN_FAIL";
    case UF_ERR_NOT_FOUND: return "NOT_FOUND";
    case UF_ERR_NOT_MATCH: return "NOT_MATCH";
    case UF_ERR_TRY_AGAIN: return "TRY_AGAIN";
    case UF_ERR_TIME_OUT: return "TIME_OUT";
    case UF_ERR_MEM_FULL: return "MEM_FULL";
    case UF_ERR_EXIST_ID: return "EXIST_ID";
    case UF_ERR_EXIST_FINGER: return "EXIST_FINGER";
    case UF_ERR_INVALID_ID: return "INVALID_ID";
    case UF_ERR_TIMEOUT_MATCH: return "TIMEOUT_MATCH";
    case UF_ERR_CANCELED: return "CANCELED";
    case UF_ERR_BUSY: return "BUSY";
    default: return "UNKNOWN";
    }
}

static void init_io_lock(void)
{
    pthread_mutexattr_t attr;
    pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(&g_io_lock, &attr);
    pthread_mutexattr_destroy(&attr);
}

static void broadcast_raw(const char *line)
{
    int i, n;
    char buf[LINE_MAX];

    pthread_once(&g_io_once, init_io_lock);

    n = snprintf(buf, sizeof(buf), "%s\n", line);
    if (n <= 0 || n >= (int)sizeof(buf)) {
        return;
    }

    pthread_mutex_lock(&g_io_lock);
    for (i = 0; i < g_nclients; ) {
        ssize_t w = send(g_clients[i], buf, (size_t)n, MSG_NOSIGNAL);
        if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            i++;
            continue;
        }
        if (w < 0) {
            close(g_clients[i]);
            g_clients[i] = g_clients[g_nclients - 1];
            g_nclients--;
            continue;
        }
        i++;
    }
    pthread_mutex_unlock(&g_io_lock);
    fprintf(stderr, "%s\n", line);
    fflush(stderr);
}

static void emit_event(const char *fmt, ...)
{
    char line[LINE_MAX];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    broadcast_raw(line);
}

static void on_enroll(BYTE errCode, UF_ENROLL_MODE enrollMode, int numOfSuccess)
{
    (void)enrollMode;
    if (errCode == UF_PROTO_RET_SCAN_SUCCESS) {
        g_enroll_scans = numOfSuccess;
        emit_event("{\"event\":\"enroll_scan\",\"count\":%d}", numOfSuccess);
    } else if (errCode == UF_PROTO_RET_CONTINUE || errCode == UF_PROTO_RET_TRY_AGAIN) {
        emit_event("{\"event\":\"enroll_retry\"}");
    }
}

static void on_identify(BYTE code)
{
    if (code == UF_PROTO_RET_SCAN_SUCCESS) {
        emit_event("{\"event\":\"identify_scan\"}");
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

static int delete_q_push_locked(UINT32 user_id)
{
    int next;
    if (user_id == 0) {
        return -1;
    }
    next = (g_delete_q_tail + 1) % DELETE_Q_MAX;
    if (next == g_delete_q_head) {
        return -1;
    }
    g_delete_q[g_delete_q_tail] = user_id;
    g_delete_q_tail = next;
    return 0;
}

static int delete_q_pop_locked(UINT32 *user_id_out)
{
    if (g_delete_q_head == g_delete_q_tail) {
        return 0;
    }
    if (user_id_out) {
        *user_id_out = g_delete_q[g_delete_q_head];
    }
    g_delete_q_head = (g_delete_q_head + 1) % DELETE_Q_MAX;
    return 1;
}

static void set_cmd(int cmd, UINT32 user_id)
{
    int prev;
    int accept;
    pthread_mutex_lock(&g_lock);
    prev = g_cmd;
    /* One slot only. delete_all must replace a queued abort, otherwise
     * factory reset's abort occupies the slot and the wipe is dropped. */
    /* Delete-all must preempt enroll/identify leftovers. A dropped wipe
     * never emits delete_all_done, so the screen waits and then fails. */
    accept = (prev == CMD_NONE) || (cmd == CMD_DELETE_ALL);
    if (accept) {
        g_cmd = cmd;
        g_cmd_user_id = user_id;
    }
    pthread_mutex_unlock(&g_lock);
    if (!accept) {
        return;
    }
    if (g_connected && g_in_identify && !g_sdk_busy) {
        UF_Cancel(FALSE);
    }
}

static int take_cmd(UINT32 *user_id_out)
{
    int cmd;
    pthread_mutex_lock(&g_lock);
    cmd = g_cmd;
    if (user_id_out) {
        *user_id_out = g_cmd_user_id;
    }
    g_cmd = CMD_NONE;
    pthread_mutex_unlock(&g_lock);
    return cmd;
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
    char found[64] = "";

    if (g_port_fixed) {
        return access(g_port, F_OK) == 0 ? 0 : -1;
    }
    for (i = 0; i < 10; i++) {
        snprintf(path, sizeof(path), "/dev/ttyACM%d", i);
        if (access(path, F_OK) == 0) {
            strncpy(found, path, sizeof(found) - 1);
            break;
        }
    }
    if (!found[0]) {
        return -1;
    }
    if (strcmp(g_port_buf, found) != 0) {
        strncpy(g_port_buf, found, sizeof(g_port_buf) - 1);
    }
    return 0;
}

static void use_saved_module_id(void)
{
    UINT32 mid = 0;

    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
    if (UF_GetSysParameter(UF_SYS_MODULE_ID, &mid) == UF_RET_SUCCESS && mid != 0) {
        UF_SetProtocol(UF_SINGLE_PROTOCOL, mid);
    }
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
            emit_event("{\"event\":\"error\",\"message\":\"no_ttyACM\"}");
            g_sdk_busy = 0;
            return -1;
        }
    }
    if (wait_for_port(10) != 0) {
        g_sdk_busy = 0;
        return -1;
    }
    usleep(50000);

    ret = UF_InitCommPort(g_port, DEFAULT_BAUD, FALSE);
    if (ret != UF_RET_SUCCESS) {
        emit_event("{\"event\":\"error\",\"message\":\"open_fail\",\"code\":\"%s\"}", ret_str(ret));
        g_sdk_busy = 0;
        return -1;
    }
    g_connected = 1;

    use_saved_module_id();
    UF_SetEnrollCallback(on_enroll);
    UF_SetIdentifyCallback(on_identify);

    ret = UF_SearchModuleID(&module_id);
    if (ret != UF_RET_SUCCESS || module_id == 0) {
        /* 일부 펌웨어/시점에서 SearchModuleID 가 NOT_FOUND 를 주지만
         * 시스템 파라미터/Identify 는 정상인 경우가 있음 → 재오픈 1회만 */
        fprintf(stderr, "SearchModuleID soft-fail (%s), retry once...\n", ret_str(ret));
        UF_CloseCommPort();
        g_connected = 0;
        sleep(1);
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
        use_saved_module_id();
        UF_SetEnrollCallback(on_enroll);
        UF_SetIdentifyCallback(on_identify);
        ret = UF_SearchModuleID(&module_id);
        /* 그래도 실패해도 Init 성공이면 진행 */
        if (ret != UF_RET_SUCCESS) {
            module_id = 0;
            fprintf(stderr, "SearchModuleID still %s — continue anyway\n", ret_str(ret));
        }
    }

    if (UF_GetSysParameter(UF_SYS_TIMEOUT, &g_saved_timeout) != UF_RET_SUCCESS) {
        g_saved_timeout = TIMEOUT_ENROLL;
    }
    UF_SetSysParameter(UF_SYS_TIMEOUT, TIMEOUT_IDENTIFY_POLL);

    emit_event("{\"event\":\"connected\",\"port\":\"%s\",\"module_id\":%u}", g_port, module_id);
    refresh_user_count();
    g_sdk_busy = 0;
    return 0;
}

static int reopen_clean(void)
{
    static int fail_streak = 0;
    int ok = open_module();
    if (ok != 0) {
        fail_streak++;
        sleep(fail_streak < 3 ? 2 : 4);
        return -1;
    }
    fail_streak = 0;
    return 0;
}

static void settle_after_scan(void)
{
    int i;
    for (i = 0; i < 10; i++) {
        if (peek_cmd() != CMD_NONE) {
            return;
        }
        usleep(100000);
    }
}

/* UF_GetAllUserInfo (zero-size list) makes this module answer BUSY to every
 * later delete. UF_GetAllUserInfoEx is the call that actually returns users. */
static int list_users_ex(UFUserInfoEx *users, UINT32 cap, UINT32 *num_user, UINT32 *num_tpl)
{
    UF_RET_CODE ret;
    *num_user = 0;
    *num_tpl = 0;
    /* A too-small legacy buffer overflows once the list succeeds. Send that
     * list once, then the extended list returns the real user ids. */
    UF_Cancel(FALSE);
    UF_SetSysParameter(UF_SYS_TIMEOUT, TIMEOUT_ENROLL);
    {
        UFUserInfo legacy[512];
        UINT32 legacy_users = 0;
        UINT32 legacy_tpl = 0;
        UF_GetAllUserInfo(legacy, &legacy_users, &legacy_tpl);
    }
    ret = UF_GetAllUserInfoEx(users, num_user, num_tpl);
    UF_SetSysParameter(UF_SYS_TIMEOUT, TIMEOUT_IDENTIFY_POLL);
    if (ret != UF_RET_SUCCESS) {
        return -1;
    }
    if (*num_user > cap) {
        *num_user = cap;
    }
    return 0;
}

static int count_users(void)
{
    static UFUserInfoEx users[512];
    UINT32 num_user = 0, num_tpl = 0;
    if (!g_connected) {
        return -1;
    }
    if (list_users_ex(users, 512, &num_user, &num_tpl) != 0) {
        return -1;
    }
    return (int)num_user;
}

static void emit_status(void)
{
    emit_event(
        "{\"event\":\"status\",\"connected\":%s,\"port\":\"%s\",\"users\":%d,\"mode\":\"%s\"}",
        g_connected ? "true" : "false",
        g_port,
        g_cached_users,
        peek_cmd() == CMD_ENROLL ? "enroll" : "identify");
}

static void refresh_user_count(void)
{
    int users = count_users();
    if (users >= 0) {
        g_cached_users = users;
    }
}

static void wait_identify_once(void)
{
    UINT32 user_id = 0;
    BYTE sub_id = 0;
    UF_RET_CODE ret;
    static int soft_fail = 0;

    if (!g_connected) {
        if (peek_cmd() != CMD_NONE) {
            return;
        }
        reopen_clean();
        return;
    }

    g_in_identify = 1;
    ret = UF_Identify(&user_id, &sub_id);
    g_in_identify = 0;

    if (peek_cmd() != CMD_NONE) {
        return;
    }

    if (ret == UF_RET_SUCCESS) {
        soft_fail = 0;
        emit_event("{\"event\":\"identify\",\"ok\":true,\"user_id\":%u,\"sub_id\":%u}",
                   user_id, (unsigned)sub_id);
        settle_after_scan();
        return;
    }
    if (ret == UF_ERR_NOT_FOUND || ret == UF_ERR_NOT_MATCH) {
        soft_fail = 0;
        emit_event("{\"event\":\"identify\",\"ok\":false,\"reason\":\"not_found\"}");
        settle_after_scan();
        return;
    }
    if (ret == UF_ERR_TIME_OUT || ret == UF_ERR_TIMEOUT_MATCH || ret == UF_ERR_CANCELED) {
        soft_fail = 0;
        return;
    }
    if (ret == UF_ERR_SCAN_FAIL) {
        soft_fail = 0;
        emit_event("{\"event\":\"identify\",\"ok\":false,\"reason\":\"scan_fail\"}");
        usleep(400000);
        return;
    }
    if (ret == UF_ERR_BUSY) {
        usleep(800000);
        return;
    }
    if (ret == UF_ERR_CANNOT_READ_SERIAL || ret == UF_ERR_CANNOT_WRITE_SERIAL ||
        ret == UF_ERR_READ_SERIAL_TIMEOUT || ret == UF_ERR_WRITE_SERIAL_TIMEOUT ||
        ret == UF_ERR_CHECKSUM_ERROR || ret == UF_ERR_COMMAND_FAILED) {
        soft_fail++;
        if (soft_fail < 5) {
            usleep(600000);
            return;
        }
        soft_fail = 0;
        reopen_clean();
        return;
    }
    usleep(500000);
}

static int ensure_module_ready(void)
{
    UINT32 mid = 0;
    UINT32 enrolled = 0;
    int i;
    if (!g_connected) {
        return reopen_clean();
    }
    for (i = 0; i < 5; i++) {
        if (UF_SearchModuleID(&mid) == UF_RET_SUCCESS && mid != 0) {
            return 0;
        }
        /* SearchModuleID 실패여도 파라미터 읽히면 통신 OK */
        if (UF_GetSysParameter(UF_SYS_ENROLLED_FINGER, &enrolled) == UF_RET_SUCCESS) {
            return 0;
        }
        usleep(300000);
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

static void do_enroll(UINT32 user_id)
{
    UINT32 enroll_id = 0, quality = 0;
    UF_ENROLL_OPTION option;
    int ret = UF_ERR_BUSY;
    int attempt;

    if (user_id == 0) {
        user_id = 1;
    }
    option = UF_ENROLL_CHECK_FINGER;
    g_enroll_scans = 0;

    emit_event("{\"event\":\"state\",\"mode\":\"enroll\",\"user_id\":%u}", user_id);

    /* Identify/Cancel 잔여 상태 제거: 포트를 깨끗이 다시 연다 */
    usleep(300000);
    if (reopen_clean() != 0) {
        emit_event("{\"event\":\"enroll_done\",\"ok\":false,\"error\":\"module_not_ready\"}");
        emit_event("{\"event\":\"state\",\"mode\":\"identify\"}");
        return;
    }

    {
        /* 같은 슬롯 재등록 시에만 해당 ID 템플릿 제거 (다른 ID에 있는 동일 지문은 유지) */
        UF_RET_CODE dr = UF_Delete(user_id);
        emit_event("{\"event\":\"enroll_prepare\",\"delete\":\"%s\",\"user_id\":%u}",
                   ret_str(dr), user_id);
        usleep(1000000);
    }

    for (attempt = 0; attempt < 3; attempt++) {
        g_enroll_scans = 0;
        emit_event("{\"event\":\"enroll_waiting\",\"user_id\":%u,\"attempt\":%d}",
                   user_id, attempt + 1);
        ret = enroll_once(user_id, option, &enroll_id, &quality);
        if (ret == UF_RET_SUCCESS) {
            break;
        }
        emit_event("{\"event\":\"enroll_retry\",\"error\":\"%s\",\"attempt\":%d}",
                   ret_str((UF_RET_CODE)ret), attempt + 1);
        /* 이미 다른 ID에 등록된 지문 → 즉시 실패 (삭제 후 재시도 금지) */
        if (ret == UF_ERR_EXIST_FINGER || ret == UF_ERR_EXIST_ID) {
            break;
        }
        if (ret == UF_ERR_BUSY || ret == UF_ERR_CANCELED ||
            ret == UF_ERR_CHECKSUM_ERROR || ret == UF_ERR_COMMAND_FAILED ||
            ret == UF_ERR_CANNOT_WRITE_SERIAL || ret == UF_ERR_CANNOT_READ_SERIAL ||
            ret == UF_ERR_READ_SERIAL_TIMEOUT) {
            usleep(800000);
            if (attempt >= 1 && reopen_clean() != 0) {
                break;
            }
            continue;
        }
        break;
    }

    if (ret == UF_RET_SUCCESS) {
        refresh_user_count();
        emit_event("{\"event\":\"enroll_done\",\"ok\":true,\"user_id\":%u,\"enroll_id\":%u,\"quality\":%u}",
                   user_id, enroll_id, quality);
    } else {
        emit_event("{\"event\":\"enroll_done\",\"ok\":false,\"user_id\":%u,\"error\":\"%s\"}",
                   user_id, ret_str((UF_RET_CODE)ret));
    }
    emit_event("{\"event\":\"state\",\"mode\":\"identify\"}");
}

static void do_delete(UINT32 user_id)
{
    UF_RET_CODE ret;
    if (user_id == 0) {
        emit_event("{\"event\":\"delete_done\",\"ok\":false,\"error\":\"invalid_id\"}");
        return;
    }
    usleep(200000);
    if (ensure_module_ready() != 0) {
        emit_event("{\"event\":\"delete_done\",\"ok\":false,\"error\":\"module_not_ready\"}");
        return;
    }
    ret = UF_Delete(user_id);
    if (ret != UF_RET_SUCCESS && ret != UF_ERR_NOT_FOUND) {
        if (reopen_clean() == 0) {
            ret = UF_Delete(user_id);
        }
    }
    emit_event("{\"event\":\"delete_done\",\"ok\":%s,\"user_id\":%u,\"error\":\"%s\"}",
               (ret == UF_RET_SUCCESS || ret == UF_ERR_NOT_FOUND) ? "true" : "false",
               user_id, ret_str(ret));
}

static UINT32 count_templates(void)
{
    UINT32 after = 0;
    if (UF_GetNumOfTemplate(&after) != UF_RET_SUCCESS) {
        return 0xffffffffu;
    }
    return after;
}

static void delete_listed_users(UFUserInfoEx *users, UINT32 num_user)
{
    UINT32 i;

    for (i = 0; i < num_user; i++) {
        UF_RET_CODE dr;
        if (users[i].userID == 0) {
            continue;
        }
        dr = UF_Delete(users[i].userID);
        if (dr != UF_RET_SUCCESS && dr != UF_ERR_NOT_FOUND) {
            usleep(200000);
            UF_Delete(users[i].userID);
        }
    }
}

static void do_delete_all(void)
{
    static UFUserInfoEx users[512];
    UF_RET_CODE ret = UF_ERR_BUSY;
    UINT32 num_user = 0;
    UINT32 num_tpl = 0;
    UINT32 after = 0xffffffffu;

    /* A zero-size user list leaves this sensor answering BUSY. Read the
     * extended list first, then wipe. */
    usleep(200000);
    if (ensure_module_ready() != 0) {
        emit_event("{\"event\":\"delete_all_done\",\"ok\":false,\"error\":\"module_not_ready\",\"templates\":-1}");
        return;
    }
    UF_Cancel(FALSE);
    UF_SetSysParameter(UF_SYS_TIMEOUT, TIMEOUT_ENROLL);
    g_sdk_busy = 1;
    if (list_users_ex(users, 512, &num_user, &num_tpl) != 0) {
        num_user = 0;
    }
    ret = UF_DeleteAll();
    usleep(400000);
    if (num_user > 0) {
        delete_listed_users(users, num_user);
        usleep(200000);
    }
    /* This module's enrolled-finger counter stays at the old number after a
     * real wipe. The user list is the count that actually changes. */
    if (list_users_ex(users, 512, &num_user, &num_tpl) == 0) {
        if (num_user > 0) {
            delete_listed_users(users, num_user);
            usleep(200000);
            if (list_users_ex(users, 512, &num_user, &num_tpl) != 0) {
                after = count_templates();
            } else {
                after = num_user;
                g_cached_users = (int)num_user;
            }
        } else {
            after = 0;
            g_cached_users = 0;
        }
    } else {
        after = count_templates();
        if (after == 0xffffffffu && ret == UF_RET_SUCCESS) {
            after = 0;
        }
    }
    UF_SetSysParameter(UF_SYS_TIMEOUT, TIMEOUT_IDENTIFY_POLL);
    g_sdk_busy = 0;
    emit_event("{\"event\":\"delete_all_done\",\"ok\":%s,\"error\":\"%s\",\"templates\":%d}",
               (after == 0) ? "true" : "false",
               (after == 0) ? "SUCCESS" : ret_str(ret),
               (after == 0xffffffffu) ? -1 : (int)after);
    emit_status();
}

static int line_has_cmd(const char *line, const char *cmd)
{
    /* "cmd":"enroll" 또는 "cmd": "enroll" 모두 허용 */
    char a[64];
    char b[64];
    snprintf(a, sizeof(a), "\"cmd\":\"%s\"", cmd);
    snprintf(b, sizeof(b), "\"cmd\": \"%s\"", cmd);
    return strstr(line, a) != NULL || strstr(line, b) != NULL;
}

static UINT32 line_user_id(const char *line, UINT32 fallback)
{
    const char *p = strstr(line, "\"user_id\"");
    if (!p) {
        return fallback;
    }
    p = strchr(p, ':');
    if (!p) {
        return fallback;
    }
    return (UINT32)strtoul(p + 1, NULL, 10);
}

static void parse_client_line(const char *line)
{
    UINT32 uid = 1;

    if (line_has_cmd(line, "ping")) {
        emit_event("{\"event\":\"pong\"}");
        return;
    }
    if (line_has_cmd(line, "status")) {
        /* Identify 블로킹과 무관하게 즉시 응답 */
        emit_status();
        return;
    }
    if (line_has_cmd(line, "abort")) {
        set_cmd(CMD_ABORT, 0);
        return;
    }
    if (line_has_cmd(line, "quit")) {
        set_cmd(CMD_QUIT, 0);
        return;
    }
    if (line_has_cmd(line, "enroll")) {
        uid = line_user_id(line, 1);
        if (uid == 0) {
            uid = 1;
        }
        set_cmd(CMD_ENROLL, uid);
        return;
    }
    if (line_has_cmd(line, "delete_all")) {
        set_cmd(CMD_DELETE_ALL, 0);
        return;
    }
    if (line_has_cmd(line, "delete")) {
        uid = line_user_id(line, 0);
        if (uid == 0) {
            return;
        }
        /* Queue deletes: factory-reset used to flood 230 cmds and all but one were dropped. */
        pthread_mutex_lock(&g_lock);
        delete_q_push_locked(uid);
        if (g_cmd == CMD_NONE) {
            g_cmd = CMD_DELETE;
            g_cmd_user_id = uid;
        }
        pthread_mutex_unlock(&g_lock);
        if (g_connected && g_in_identify && !g_sdk_busy) {
            UF_Cancel(FALSE);
        }
        return;
    }
}

static void *socket_thread(void *arg)
{
    (void)arg;
    pthread_once(&g_io_once, init_io_lock);
    while (g_running) {
        struct pollfd pfds[MAX_CLIENTS + 1];
        int nfds = 0;
        int i, pr;
        char line[LINE_MAX];
        ssize_t n;

        pfds[0].fd = g_listen_fd;
        pfds[0].events = POLLIN;
        nfds = 1;

        pthread_mutex_lock(&g_io_lock);
        for (i = 0; i < g_nclients; i++) {
            pfds[nfds].fd = g_clients[i];
            pfds[nfds].events = POLLIN;
            nfds++;
        }
        pthread_mutex_unlock(&g_io_lock);

        pr = poll(pfds, (nfds_t)nfds, 300);
        if (pr < 0) {
            if (errno == EINTR) {
                continue;
            }
            usleep(50000);
            continue;
        }

        if (pfds[0].revents & POLLIN) {
            int cfd = accept(g_listen_fd, NULL, NULL);
            if (cfd >= 0) {
                int flags = fcntl(cfd, F_GETFL, 0);
                if (flags >= 0) {
                    /* keep blocking for reliable small event writes */
                    fcntl(cfd, F_SETFL, flags & ~O_NONBLOCK);
                }
                pthread_mutex_lock(&g_io_lock);
                if (g_nclients < MAX_CLIENTS) {
                    g_clients[g_nclients++] = cfd;
                    pthread_mutex_unlock(&g_io_lock);
                    {
                        const char *hello = "{\"event\":\"hello\"}\n";
                        ssize_t w = write(cfd, hello, strlen(hello));
                        if (w < 0) {
                            fprintf(stderr, "hello write fail errno=%d\n", errno);
                        }
                    }
                    emit_status();
                } else {
                    pthread_mutex_unlock(&g_io_lock);
                    close(cfd);
                }
            }
        }

        for (i = 1; i < nfds; i++) {
            if (!(pfds[i].revents & (POLLIN | POLLHUP | POLLERR))) {
                continue;
            }
            n = read(pfds[i].fd, line, sizeof(line) - 1);
            if (n <= 0) {
                pthread_mutex_lock(&g_io_lock);
                {
                    int j;
                    for (j = 0; j < g_nclients; j++) {
                        if (g_clients[j] == pfds[i].fd) {
                            close(g_clients[j]);
                            g_clients[j] = g_clients[g_nclients - 1];
                            g_nclients--;
                            break;
                        }
                    }
                }
                pthread_mutex_unlock(&g_io_lock);
                continue;
            }
            line[n] = '\0';
            {
                char *save = NULL;
                char *tok = strtok_r(line, "\n", &save);
                while (tok) {
                    while (*tok && isspace((unsigned char)*tok)) {
                        tok++;
                    }
                    if (*tok) {
                        parse_client_line(tok);
                    }
                    tok = strtok_r(NULL, "\n", &save);
                }
            }
        }
    }
    return NULL;
}

static int setup_socket(void)
{
    struct sockaddr_un addr;
    char *dir;
    char *slash;

    dir = strdup(g_sock_path);
    if (!dir) {
        return -1;
    }
    slash = strrchr(dir, '/');
    if (slash) {
        *slash = '\0';
        mkdir(dir, 0755);
    }
    free(dir);

    unlink(g_sock_path);
    g_listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (g_listen_fd < 0) {
        perror("socket");
        return -1;
    }
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, g_sock_path, sizeof(addr.sun_path) - 1);
    if (bind(g_listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        return -1;
    }
    chmod(g_sock_path, 0660);
    if (listen(g_listen_fd, 32) < 0) {
        perror("listen");
        return -1;
    }
    return 0;
}

static void on_signal(int sig)
{
    (void)sig;
    g_running = 0;
    if (g_connected && g_in_identify && !g_sdk_busy) {
        UF_Cancel(FALSE);
    }
}

int main(int argc, char **argv)
{
    pthread_t tid;
    int major = 0, minor = 0, revision = 0;
    UINT32 uid = 1;
    int cmd;

    if (argc >= 2) {
        strncpy(g_port_buf, argv[1], sizeof(g_port_buf) - 1);
        g_port_fixed = 1;
    }
    if (argc >= 3) {
        strncpy(g_sock_path, argv[2], sizeof(g_sock_path) - 1);
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);

    UF_GetSDKVersion(&major, &minor, &revision);
    fprintf(stderr, "sfm_daemon SDK %d.%d.%d sock=%s\n", major, minor, revision, g_sock_path);

    if (setup_socket() != 0) {
        return 1;
    }
    if (open_module() != 0) {
        fprintf(stderr, "initial open failed; will retry in loop\n");
    }

    if (pthread_create(&tid, NULL, socket_thread, NULL) != 0) {
        perror("pthread_create");
        return 1;
    }

    emit_event("{\"event\":\"state\",\"mode\":\"identify\"}");

    while (g_running) {
        cmd = take_cmd(&uid);
        if (cmd != CMD_NONE) {
            while (g_in_identify) {
                usleep(10000);
            }
            usleep(30000);
            switch (cmd) {
            case CMD_QUIT:
                g_running = 0;
                break;
            case CMD_ENROLL:
                do_enroll(uid);
                break;
            case CMD_DELETE:
                while (1) {
                    UINT32 duid = 0;
                    int has;
                    pthread_mutex_lock(&g_lock);
                    has = delete_q_pop_locked(&duid);
                    pthread_mutex_unlock(&g_lock);
                    if (!has) {
                        break;
                    }
                    do_delete(duid);
                }
                break;
            case CMD_DELETE_ALL:
                pthread_mutex_lock(&g_lock);
                g_delete_q_head = g_delete_q_tail = 0;
                pthread_mutex_unlock(&g_lock);
                do_delete_all();
                break;
            case CMD_ABORT:
                if (g_connected && !g_sdk_busy) {
                    UF_Cancel(FALSE);
                }
                emit_event("{\"event\":\"aborted\"}");
                emit_event("{\"event\":\"state\",\"mode\":\"identify\"}");
                break;
            case CMD_STATUS:
                refresh_user_count();
                emit_status();
                break;
            default:
                break;
            }
            continue;
        }
        wait_identify_once();
    }

    if (g_connected && !g_sdk_busy) {
        if (g_in_identify) {
            UF_Cancel(TRUE);
        }
        UF_SetSysParameter(UF_SYS_TIMEOUT, g_saved_timeout);
    }
    close_module();
    unlink(g_sock_path);
    close(g_listen_fd);
    pthread_join(tid, NULL);
    return 0;
}
