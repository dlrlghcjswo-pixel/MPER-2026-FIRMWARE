#include <stdio.h>
#include "UF_API.h"

int main(void)
{
    UINT32 mode = 0, timeout = 0, send_scan = 0;

    if (UF_InitCommPort("/dev/ttyACM0", 115200, FALSE) != UF_RET_SUCCESS) {
        printf("open failed\n");
        return 1;
    }
    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);

    UF_ReadSysParameter(UF_SYS_ENROLL_MODE, &mode);
    UF_ReadSysParameter(UF_SYS_TIMEOUT, &timeout);
    UF_ReadSysParameter(UF_SYS_SEND_SCAN_SUCCESS, &send_scan);

    printf("ENROLL_MODE=0x%02X\n", mode);
    printf("TIMEOUT=0x%02X\n", timeout);
    printf("SEND_SCAN_SUCCESS=0x%02X\n", send_scan);

    if (mode == UF_ENROLL_ONE_TIME)
        printf("=> ONE_TIME: fingerprint once\n");
    else if (mode == UF_ENROLL_TWO_TIMES1)
        printf("=> TWO_TIMES1: same finger twice\n");
    else if (mode == UF_ENROLL_TWO_TIMES2)
        printf("=> TWO_TIMES2: same finger twice (alt)\n");
    else if (mode == UF_ENROLL_TWO_TEMPLATES1)
        printf("=> TWO_TEMPLATES1: two templates\n");
    else if (mode == UF_ENROLL_TWO_TEMPLATES2)
        printf("=> TWO_TEMPLATES2: two templates (alt)\n");
    else
        printf("=> unknown enroll mode\n");

    UF_CloseCommPort();
    return 0;
}
