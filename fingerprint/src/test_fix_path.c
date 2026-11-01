#include <stdio.h>
#include <unistd.h>
#include "UF_API.h"

int main(void)
{
    UINT32 uid = 0, eid = 0, q = 0;
    BYTE sid = 0;
    UF_RET_CODE r;

    UF_InitCommPort("/dev/ttyACM0", 115200, FALSE);
    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
    UF_SetSysParameter(UF_SYS_TIMEOUT, 0x14);
    r = UF_Identify(&uid, &sid);
    printf("id1=%d\n", (int)r);
    r = UF_Identify(&uid, &sid);
    printf("id2=%d\n", (int)r);

    UF_CloseCommPort();
    usleep(200000);

    UF_InitCommPort("/dev/ttyACM0", 115200, FALSE);
    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
    UF_SetSysParameter(UF_SYS_TIMEOUT, 0x3C);
    printf("enroll start\n");
    fflush(stdout);
    r = UF_Enroll(88, UF_ENROLL_ADD_NEW, &eid, &q);
    printf("enroll=%d\n", (int)r);
    UF_CloseCommPort();
    return 0;
}
