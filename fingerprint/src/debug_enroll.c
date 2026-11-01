#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "UF_API.h"

static void on_enroll(BYTE errCode, UF_ENROLL_MODE enrollMode, int numOfSuccess)
{
    (void)enrollMode;
    printf("  enroll_cb 0x%02X success=%d\n", errCode, numOfSuccess);
    fflush(stdout);
}

static int open_port(void)
{
    UF_RET_CODE ret = UF_InitCommPort("/dev/ttyACM0", 115200, FALSE);
    printf("Init=%d\n", (int)ret);
    if (ret != UF_RET_SUCCESS) return -1;
    UF_SetProtocol(UF_SINGLE_PROTOCOL, 0);
    UF_SetEnrollCallback(on_enroll);
    return 0;
}

int main(int argc, char **argv)
{
    int mode = argc >= 2 ? atoi(argv[1]) : 0;
    UINT32 enroll_id = 0, quality = 0, mid = 0;
    UF_RET_CODE ret;

    if (open_port() != 0) return 1;
    UF_SearchModuleID(&mid);
    printf("module=%u test_mode=%d\n", mid, mode);

    if (mode == 1) {
        UINT32 uid = 0; BYTE sid = 0;
        printf("step: identify then cancel then enroll\n");
        ret = UF_Identify(&uid, &sid);
        printf("identify=%d\n", (int)ret);
        UF_Cancel(TRUE);
        usleep(500000);
    } else if (mode == 2) {
        printf("step: cancel only then enroll\n");
        UF_Cancel(TRUE);
        usleep(500000);
    } else if (mode == 3) {
        printf("step: close/reopen then enroll\n");
        UF_CloseCommPort();
        usleep(300000);
        if (open_port() != 0) return 1;
    } else if (mode == 4) {
        UINT32 uid = 0; BYTE sid = 0;
        printf("step: identify+cancel+reopen then enroll\n");
        ret = UF_Identify(&uid, &sid);
        printf("identify=%d\n", (int)ret);
        UF_Cancel(TRUE);
        UF_CloseCommPort();
        usleep(300000);
        if (open_port() != 0) return 1;
    } else {
        printf("step: enroll only (no identify)\n");
    }

    printf("ENROLL start - LED should turn on now\n");
    fflush(stdout);
    ret = UF_Enroll(99, UF_ENROLL_ADD_NEW, &enroll_id, &quality);
    printf("enroll_ret=%d id=%u q=%u\n", (int)ret, enroll_id, quality);
    UF_CloseCommPort();
    return 0;
}
