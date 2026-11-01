#include <stdio.h>
#include <unistd.h>
#include "UF_API.h"

static const char *port_pick(void)
{
    if (access("/dev/ttyACM0", F_OK) == 0) return "/dev/ttyACM0";
    if (access("/dev/ttyACM1", F_OK) == 0) return "/dev/ttyACM1";
    return "/dev/ttyACM0";
}

static void counts(const char *tag)
{
    UINT32 n = 0, m = 0, e = 0;
    printf("[%s] GetNum=%d n=%u  GetMax=%d m=%u  EnrolledParam=%d e=%u\n",
           tag,
           (int)UF_GetNumOfTemplate(&n), n,
           (int)UF_GetMaxNumOfTemplate(&m), m,
           (int)UF_ReadSysParameter(UF_SYS_ENROLLED_FINGER, &e), e);
    fflush(stdout);
}

static void list_users(void)
{
    UFUserInfo users[512];
    UINT32 nu = 0, nt = 0;
    UF_RET_CODE r = UF_GetAllUserInfo(users, &nu, &nt);
    UINT32 i;
    printf("GetAllUserInfo ret=%d users=%u templates=%u\n", (int)r, nu, nt);
    for (i = 0; i < nu && i < 64; i++) {
        printf("  id=%u numTpl=%u admin=%u\n",
               users[i].userID, users[i].numOfTemplate,
               users[i].adminLevel);
    }
    fflush(stdout);
}

int main(void)
{
    const char *port = port_pick();
    UF_RET_CODE r;
    UINT32 mid = 0;
    UINT32 i;
    UFUserInfo users[512];
    UINT32 nu = 0, nt = 0;

    r = UF_InitCommPort(port, 115200, FALSE);
    printf("Init(%s)=%d\n", port, (int)r);
    if (r != UF_RET_SUCCESS) return 1;
    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
    r = UF_SearchModuleID(&mid);
    printf("SearchModuleID=%d mid=%u\n", (int)r, mid);

    counts("before");
    list_users();

    /* 사용자별 개별 삭제 */
    r = UF_GetAllUserInfo(users, &nu, &nt);
    if (r == UF_RET_SUCCESS && nu > 0) {
        printf("Deleting each user one by one (%u users)...\n", nu);
        for (i = 0; i < nu; i++) {
            r = UF_Delete(users[i].userID);
            printf("  UF_Delete(%u)=%d\n", users[i].userID, (int)r);
        }
        usleep(300000);
        counts("after per-user delete");
        list_users();
    }

    r = UF_DeleteAll();
    printf("UF_DeleteAll=%d\n", (int)r);
    usleep(300000);
    counts("after DeleteAll");
    list_users();

    /* CheckTemplate for ids 1..20 */
    printf("CheckTemplate 1..20:\n");
    for (i = 1; i <= 20; i++) {
        UINT32 ntpl = 0;
        r = UF_CheckTemplate(i, &ntpl);
        if (r == UF_RET_SUCCESS && ntpl > 0) {
            printf("  id=%u templates=%u\n", i, ntpl);
        }
    }

    UF_CloseCommPort();
    return 0;
}
