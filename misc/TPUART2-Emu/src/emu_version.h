/*
 *  emu_version.h - Name and version of the TP-UART emulator.
 *
 *  The one place the version is set. APP_VERSION in app_main.cpp and the
 *  answer to U_EmuIdentify both take it from here. Keep the build variable
 *  sw_version of the MCUXpresso project and the CMake project version in
 *  step with it, they only name the output files.
 *
 *  Raise the minor version with every change of the behaviour: a host can
 *  only tell builds apart by what U_EmuIdentify reports. The build id, the
 *  Git commit of the build, comes from src/emu_build_id.h, which
 *  tools/emu_build_id.sh writes before every build.
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License version 3 as
 *  published by the Free Software Foundation.
 */
#ifndef TPUART_EMU_VERSION_H_
#define TPUART_EMU_VERSION_H_

#include <cstdint>

/** Application name, exactly 8 characters (APP_VERSION and U_EmuIdentify). */
#define TPUART_EMU_APP_NAME      "SBtpuart"
/** Major version, exactly 1 digit. */
#define TPUART_EMU_VERSION_MAJOR "0"
/** Minor version, exactly 2 digits. */
#define TPUART_EMU_VERSION_MINOR "14"

#if __has_include("emu_build_id.h")
#   include "emu_build_id.h"
#else
/** First 8 hex digits of the Git commit of the build, 0 if unknown. */
#   define TPUART_EMU_BUILD_ID    (0u)
/** 1 if tracked files of the project differed from that commit. */
#   define TPUART_EMU_BUILD_DIRTY (0)
#endif

static_assert(sizeof(TPUART_EMU_APP_NAME) == 8 + 1, "APP_VERSION needs an app name of 8 characters");
static_assert(sizeof(TPUART_EMU_VERSION_MAJOR) == 1 + 1, "APP_VERSION needs a major version of 1 digit");
static_assert(sizeof(TPUART_EMU_VERSION_MINOR) == 2 + 1, "APP_VERSION needs a minor version of 2 digits");

/** Value of a string of decimal digits, for the numeric version in U_EmuIdentify. */
constexpr uint8_t emuDecimalValue(const char* digits)
{
    uint8_t value = 0;
    while (*digits != '\0')
    {
        value = static_cast<uint8_t>(value * 10 + (*digits++ - '0'));
    }
    return value;
}

constexpr uint8_t TPUART_EMU_MAJOR = emuDecimalValue(TPUART_EMU_VERSION_MAJOR);
constexpr uint8_t TPUART_EMU_MINOR = emuDecimalValue(TPUART_EMU_VERSION_MINOR);

#endif /* TPUART_EMU_VERSION_H_ */
