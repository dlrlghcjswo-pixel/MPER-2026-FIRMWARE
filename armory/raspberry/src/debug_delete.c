#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "UF_API.h"

static const char *pick_port(void)
{
    if (access("/dev/ttyACM0", F_OK) == 0) return "/dev/ttyACM0";
    if (access("/dev/ttyACM1", F_OK) == 0) return "/dev/ttyACM1";
    return "/dev/ttyACM0";
}

static void show_counts(const char *tag)
{
    UINT32 num = 0, maxn = 0, enrolled = 0;
    UF_RET_CODE r1, r2, r3;
    r1 = UF_GetNumOfTemplate(&num);
    r2 = UF_GetMaxNumOfTemplate(&maxn);
    r3 = UF_ReadSysParameter(UF_SYS_ENROLLED_FINGER, &enrolled);
    printf("[%s] num=%u (ret=%d) max=%u (ret=%d) enrolled_param=%u (ret=%d)\n",
           tag, num, (int)r1, maxn, (int)r2, enrolled, (int)r3);
    fflush(stdout);
}

static void list_users(void)
{
    UFUserInfo users[256];
    UINT32 numUser = 0, numTemplate = 0;
    UF_RET_CODE r;
    UINT32 i;

    r = UF_GetAllUserInfo(users, &numUser, &numTemplate);
    printf("GetAllUserInfo ret=%d users=%u templates=%u\n", (int)r, numUser, numTemplate);
    for (i = 0; i < numUser && i < 32; i++) {
        printf("  userID=%u templates=%u\n",
               (unsigned)users[i].userID, (unsigned)users[i].numOfTemplate);
    }
    fflush(stdout);
}

int main(void)
{
    const char *port = pick_port();
    UF_RET_CODE r;
    UINT32 mid = 0;
    int deleted = 0;

    r = UF_InitCommPort(port, 115200, FALSE);
    printf("Init(%s)=%d\n", port, (int)r);
    if (r != UF_RET_SUCCESS) return 1;
    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
    UF_SearchModuleID(&mid);
    printf("module=%u\n", mid);

    show_counts("before");
    list_users();

    r = UF_DeleteAll();
    printf("UF_DeleteAll -> %d\n", (int)r);
    usleep(500000);
    show_counts("after DeleteAll");
    list_users();

    r = UF_DeleteMultipleTemplates(1, 20000, &deleted);
    printf("UF_DeleteMultipleTemplates(1..20000) -> %d deletedUserID=%d\n", (int)r, deleted);
    usleep(500000);
    show_counts("after DeleteMultiple");
    list_users();

    UF_CloseCommPort();
    return 0;
}
