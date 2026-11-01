#include <stdio.h>
#include <unistd.h>
#include "UF_API.h"

int main(void)
{
    UF_RET_CODE r;
    UINT32 eid = 0, q = 0, mid = 0;

    r = UF_InitCommPort("/dev/ttyACM0", 115200, FALSE);
    printf("init=%d\n", (int)r);
    if (r != UF_RET_SUCCESS) {
        return 1;
    }

    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
    printf("cancel...\n");
    UF_Cancel(FALSE);
    usleep(200000);

    printf("reset...\n");
    r = UF_Reset();
    printf("reset=%d\n", (int)r);
    usleep(2000000);

    UF_CloseCommPort();
    usleep(500000);

    r = UF_InitCommPort("/dev/ttyACM0", 115200, FALSE);
    printf("reinit=%d\n", (int)r);
    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
    r = UF_SearchModuleID(&mid);
    printf("search=%d mid=%u\n", (int)r, mid);

    printf("enroll try (should light LED / wait)\n");
    fflush(stdout);
    r = UF_Enroll(77, UF_ENROLL_ADD_NEW, &eid, &q);
    printf("enroll=%d eid=%u q=%u\n", (int)r, eid, q);
    UF_CloseCommPort();
    return 0;
}
