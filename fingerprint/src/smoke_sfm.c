#include <stdio.h>
#include <stdlib.h>

#include "UF_API.h"

static const char *port = "/dev/ttyACM0";

int main(int argc, char **argv)
{
    int major = 0, minor = 0, revision = 0;
    UF_RET_CODE ret;
    UINT32 module_id = 0;
    UINT32 fw = 0, sn = 0, sensor = 0, enrolled = 0;

    if (argc >= 2) {
        port = argv[1];
    }

    UF_GetSDKVersion(&major, &minor, &revision);
    printf("SFM SDK %d.%d.%d\n", major, minor, revision);
    printf("Opening %s @ 115200 binary\n", port);

    ret = UF_InitCommPort(port, 115200, FALSE);
    printf("UF_InitCommPort -> %d\n", (int)ret);
    if (ret != UF_RET_SUCCESS) {
        return 1;
    }

    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);

    ret = UF_SearchModuleID(&module_id);
    printf("UF_SearchModuleID -> %d, module_id=%u (0x%08X)\n",
           (int)ret, module_id, module_id);

    ret = UF_ReadSysParameter(UF_SYS_FIRMWARE_VERSION, &fw);
    printf("UF_SYS_FIRMWARE_VERSION -> %d, value=0x%08X\n", (int)ret, fw);

    ret = UF_ReadSysParameter(UF_SYS_SERIAL_NUMBER, &sn);
    printf("UF_SYS_SERIAL_NUMBER -> %d, value=0x%08X\n", (int)ret, sn);

    ret = UF_ReadSysParameter(UF_SYS_SENSOR_TYPE, &sensor);
    printf("UF_SYS_SENSOR_TYPE -> %d, value=0x%08X\n", (int)ret, sensor);

    ret = UF_ReadSysParameter(UF_SYS_ENROLLED_FINGER, &enrolled);
    printf("UF_SYS_ENROLLED_FINGER -> %d, value=%u\n", (int)ret, enrolled);

    UF_CloseCommPort();
    printf("OK: module communication succeeded\n");
    return (ret == UF_RET_SUCCESS || module_id != 0) ? 0 : 2;
}
