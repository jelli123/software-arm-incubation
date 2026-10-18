/*
 *  tpuart_defs.h - Service codes of the KNX TP-UART host protocol.
 *
 *  The values follow the Siemens TP-UART 2 (and ON Semi NCN512x) host
 *  interface description. Only the subset that is actually emulated is
 *  listed here, see README.md for the supported services.
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License version 3 as
 *  published by the Free Software Foundation.
 */
#ifndef TPUART_DEFS_H_
#define TPUART_DEFS_H_

#include <cstdint>

/**
 * Services sent by the host to the transceiver.
 */
enum TpUartHostService : uint8_t
{
    U_RESET_REQ             = 0x01, //!< reset the transceiver
    U_STATE_REQ             = 0x02, //!< request @ref U_STATE_IND
    U_SET_BUSY_REQ          = 0x03, //!< stop acknowledging received frames
    U_QUIT_BUSY_REQ         = 0x04, //!< resume acknowledging received frames
    U_BUSMON_REQ            = 0x05, //!< enter bus monitor mode (leave via reset)
    U_L_DATA_OFFSET_REQ     = 0x08, //!< 0x08..0x0C, offset = cmd & 0x07, unit 64 byte
    U_SYSTEM_STATE_REQ      = 0x0D, //!< request @ref U_SYSTEM_STAT_IND (NCN512x service)
    U_STOP_MODE_REQ         = 0x0E, //!< detach from the bus
    U_EXIT_STOP_MODE_REQ    = 0x0F, //!< re-attach to the bus
    U_ACK_INFORMATION_REQ   = 0x10, //!< 0x10..0x17, see @ref TpUartAckInformation
    U_CONFIGURE_REQ         = 0x18, //!< 0x18..0x1F, NCN512x only, ignored
    U_PRODUCT_ID_REQ        = 0x20, //!< request @ref TPUART_PRODUCT_ID
    U_ACTIVATE_BUSY_MODE_REQ = 0x21, //!< stop acknowledging addressed frames for @ref TPUART_BUSY_MODE_MS
    U_RESET_BUSY_MODE_REQ   = 0x22, //!< end the busy mode immediately
    U_MXRSTCNT_REQ          = 0x24, //!< 0x24 + repetition byte, TP-UART 2 only
    U_ACTIVATE_CRC_REQ      = 0x25, //!< append a CRC16-CCITT to every L_Data.ind
    U_SET_ADDRESS_REQ       = 0x28, //!< 0x28 + addrHigh + addrLow (used by knxd)
    U_INT_REG_WR_REQ        = 0x29, //!< 0x29..0x2B, NCN512x only, ignored
    U_INT_REG_RD_REQ        = 0x38, //!< 0x38..0x3B, NCN512x only, ignored
    U_L_DATA_END_REQ        = 0x40, //!< 0x40..0x7F, last byte of a frame
    U_L_DATA_START_CONT_REQ = 0x80, //!< 0x80..0xBF, byte n of a frame
    U_POLLING_STATE_REQ     = 0xE0, //!< 0xE0..0xEE + pollAddrHigh + pollAddrLow + state, ignored
    U_SET_ADDRESS_REQ_ALT   = 0xF1, //!< 0xF1 + addrHigh + addrLow (used by OpenKNX)
    U_SET_REPETITION_REQ    = 0xF2, //!< 0xF2 + repetition byte + 2 dummy bytes, NCN512x only
    U_EMU_CONFIGURE_REQ     = 0xFD, //!< Selfbus extension, 0xFD + rate + flags, see @ref TpUartEmuRate
    U_EMU_IDENTIFY_REQ      = 0xFE  //!< Selfbus extension, request @ref TpUartEmuIdentify
};

constexpr uint8_t U_L_DATA_OFFSET_REQ_MAX     = 0x0C; //!< last code of @ref U_L_DATA_OFFSET_REQ
constexpr uint8_t U_ACK_INFORMATION_REQ_MAX   = 0x17; //!< last code of @ref U_ACK_INFORMATION_REQ
constexpr uint8_t U_INT_REG_WR_REQ_MAX        = 0x2B; //!< last code of @ref U_INT_REG_WR_REQ
constexpr uint8_t U_L_DATA_END_REQ_MAX        = 0x7F; //!< last code of @ref U_L_DATA_END_REQ
constexpr uint8_t U_L_DATA_START_CONT_REQ_MAX = 0xBF; //!< last code of @ref U_L_DATA_START_CONT_REQ
constexpr uint8_t U_POLLING_STATE_REQ_MAX     = 0xEE; //!< last code of @ref U_POLLING_STATE_REQ

constexpr uint8_t U_L_DATA_OFFSET_MASK = 0x07; //!< offset part of @ref U_L_DATA_OFFSET_REQ
constexpr uint8_t U_L_DATA_INDEX_MASK  = 0x3F; //!< octet index part of U_L_DataStart/Cont/End
constexpr uint8_t U_L_DATA_OFFSET_UNIT = 64;   //!< octets per @ref U_L_DATA_OFFSET_REQ step

/**
 * Flags of the @ref U_ACK_INFORMATION_REQ service.
 */
enum TpUartAckInformation : uint8_t
{
    U_ACK_REQ_ADDRESSED = 0x01, //!< the frame is addressed to the host
    U_ACK_REQ_BUSY      = 0x02, //!< answer with BUSY
    U_ACK_REQ_NACK      = 0x04  //!< answer with NACK
};

/**
 * Services sent by the transceiver to the host.
 */
enum TpUartControllerService : uint8_t
{
    L_DATA_STANDARD_IND = 0x90, //!< masked with @ref L_DATA_MASK
    L_DATA_EXTENDED_IND = 0x10, //!< masked with @ref L_DATA_MASK
    L_DATA_MASK         = 0xD3, //!< mask to detect an L_Data frame start
    L_DATA_CON          = 0x0B, //!< L_Data.con, or'ed with @ref L_DATA_CON_SUCCESS
    L_DATA_CON_SUCCESS  = 0x80, //!< positive confirmation
    U_RESET_IND         = 0x03, //!< answer to @ref U_RESET_REQ
    U_STATE_IND         = 0x07, //!< answer to @ref U_STATE_REQ, or'ed with error flags
    U_STOP_MODE_IND     = 0x2B, //!< answer to @ref U_STOP_MODE_REQ
    U_SYSTEM_STAT_IND   = 0x4B  //!< answer to @ref U_SYSTEM_STATE_REQ, followed by @ref TpUartSystemState
};

/**
 * Second octet of @ref U_SYSTEM_STAT_IND, laid out as on an NCN5120.
 *
 * VBUS follows KNX_OK and VDD2 follows VCC_OK of the STKNX. The emulator does
 * not monitor the other supplies and the crystal, those bits are always set.
 */
enum TpUartSystemState : uint8_t
{
    TPUART_SYSTEM_XTAL         = 0x80, //!< crystal oscillator in range
    TPUART_SYSTEM_VFILT        = 0x40, //!< tank capacitor voltage in range
    TPUART_SYSTEM_VBUS         = 0x20, //!< KNX bus voltage in range
    TPUART_SYSTEM_VDD2         = 0x10, //!< DC2 regulator in range
    TPUART_SYSTEM_V20V         = 0x08, //!< 20 V regulator in range
    TPUART_SYSTEM_TW           = 0x04, //!< thermal warning
    TPUART_SYSTEM_MODE_POWERUP = 0x00, //!< no communication with the bus
    TPUART_SYSTEM_MODE_SYNC    = 0x01, //!< waiting for silence on the bus
    TPUART_SYSTEM_MODE_STOP    = 0x02, //!< after @ref U_STOP_MODE_REQ
    TPUART_SYSTEM_MODE_NORMAL  = 0x03  //!< fully functional
};

/**
 * Error flags reported in @ref U_STATE_IND.
 */
enum TpUartStateFlags : uint8_t
{
    TPUART_SLAVE_COLLISION     = 0x80,
    TPUART_RECEIVE_ERROR       = 0x40,
    TPUART_TRANSMIT_ERROR      = 0x20,
    TPUART_PROTOCOL_ERROR      = 0x10,
    TPUART_TEMPERATURE_WARNING = 0x08
};

/**
 * Answer to @ref U_EMU_IDENTIFY_REQ, a Selfbus extension.
 *
 * Tells a host that the transceiver is this emulator and which firmware it
 * runs - a real TP-UART 2 answers the request with a protocol error, and to
 * @ref U_PRODUCT_ID_REQ the emulator answers exactly like one. All octets are
 * sent as one block, telegrams from the bus never come in between.
 *
 * | Octet | Content                                                   |
 * | ----- | --------------------------------------------------------- |
 * | 0     | @ref U_EMU_IDENTIFY_REQ, as start of the answer           |
 * | 1     | number of the octets 2..n-2 (@ref TPUART_EMU_IDENTIFY_LENGTH) |
 * | 2     | format of the answer (@ref TPUART_EMU_IDENTIFY_FORMAT)    |
 * | 3, 4  | firmware version, major and minor                         |
 * | 5, 6  | sblib version, high and low octet of SBLIB_VERSION        |
 * | 7     | longest frame incl. checksum (@ref TPUART_MAX_FRAME_SIZE) |
 * | 8..15 | application name, 8 ASCII characters                      |
 * | 16    | features, see @ref TpUartEmuFeature (format 2 and later)  |
 * | 17    | highest rate code of @ref U_EMU_CONFIGURE_REQ (format 2)  |
 * | n-1   | checksum: all octets before it XOR'ed, then inverted      |
 *
 * A host has to accept a longer answer of a later format and ignore the
 * octets it does not know; their number is in octet 1.
 */
enum TpUartEmuIdentify : uint8_t
{
    TPUART_EMU_IDENTIFY_FORMAT = 2,  //!< format described above
    TPUART_EMU_IDENTIFY_LENGTH = 16  //!< octets 2..17
};

/**
 * Feature bits in octet 16 of the answer to @ref U_EMU_IDENTIFY_REQ.
 */
enum TpUartEmuFeature : uint8_t
{
    TPUART_EMU_FEATURE_CONFIGURE    = 0x01, //!< understands @ref U_EMU_CONFIGURE_REQ
    TPUART_EMU_FEATURE_READY        = 0x02, //!< drives /LPC_READY, see PIN_LPC_READY
    TPUART_EMU_FEATURE_SYSTEM_STATE = 0x04  //!< answers @ref U_SYSTEM_STATE_REQ
};

/**
 * Rate codes of @ref U_EMU_CONFIGURE_REQ, a Selfbus extension.
 *
 * The host sends 0xFD, a rate code and @ref TpUartEmuConfigureFlags. The
 * emulator answers at the old rate with 0xFD, rate, flags and a checksum (all
 * octets before it XOR'ed, then inverted), and switches once that answer has
 * left the UART. The host switches when it has the answer, and sends nothing
 * in between. Invalid values are answered with a protocol error, nothing
 * changes then.
 *
 * U_Reset.req keeps the rate, so the reset handshake of a host does not break
 * the link. Back to @ref TPUART_EMU_RATE_19200 and paced output it goes on a
 * framing, parity or break error and after @ref TPUART_EMU_RATE_IDLE_MS
 * without a single octet from the host - which is what a host that restarted
 * at 19200 baud causes on its first request.
 *
 * Only to be sent after @ref U_EMU_IDENTIFY_REQ reported
 * @ref TPUART_EMU_FEATURE_CONFIGURE: an emulator before 0.11 and a real
 * TP-UART would take the two data octets for services of their own.
 */
enum TpUartEmuRate : uint8_t
{
    TPUART_EMU_RATE_19200  = 0, //!< TP-UART 2 default, after power up
    TPUART_EMU_RATE_38400  = 1,
    TPUART_EMU_RATE_57600  = 2,
    TPUART_EMU_RATE_115200 = 3,
    TPUART_EMU_RATE_MAX    = TPUART_EMU_RATE_115200 //!< highest code, reported by U_EmuIdentify
};

/**
 * Flags of @ref U_EMU_CONFIGURE_REQ.
 */
enum TpUartEmuConfigureFlags : uint8_t
{
    TPUART_EMU_FLAG_BURST = 0x01, //!< send to the host without pacing, see TPUART_TX_PACING_MS
    TPUART_EMU_FLAGS_KNOWN = TPUART_EMU_FLAG_BURST
};

/** Answer to @ref U_PRODUCT_ID_REQ: product id 2, revision 1 (TP-UART 2, release a). */
constexpr uint8_t TPUART_PRODUCT_ID = 0x41;
/** Duration of the busy mode started by @ref U_ACTIVATE_BUSY_MODE_REQ. */
constexpr uint32_t TPUART_BUSY_MODE_MS = 700;
/**
 * Initial value of the CRC16-CCITT enabled by @ref U_ACTIVATE_CRC_REQ.
 * The data sheet specifies init value FFFFh for the augmented algorithm (test string
 * "123456789" gives E5CCh). The direct algorithm used here needs 1D0Fh for the same result.
 */
constexpr uint16_t TPUART_CRC_INIT = 0x1D0F;
/** Polynomial of the CRC16-CCITT enabled by @ref U_ACTIVATE_CRC_REQ. */
constexpr uint16_t TPUART_CRC_POLYNOMIAL = 0x1021;

/**
 * Maximum size of a KNX frame including its checksum octet. Also the size of
 * sblib's receive buffer. An extended frame of this size carries an APDU of
 * 64 - @ref LPDU_EXT_OVERHEAD = 55 octets.
 */
constexpr uint16_t TPUART_MAX_FRAME_SIZE = 64;
/** Number of octets a standard L_Data frame has in addition to its payload. */
constexpr uint16_t LPDU_STD_OVERHEAD = 8;
/** Number of octets an extended L_Data frame has in addition to its payload. */
constexpr uint16_t LPDU_EXT_OVERHEAD = 9;
/** Index of the high octet of the source address of a standard L_Data frame. */
constexpr uint8_t LPDU_STD_SOURCE_OCTET = 1;
/** Index of the high octet of the source address of an extended L_Data frame. */
constexpr uint8_t LPDU_EXT_SOURCE_OCTET = 2;

#endif /* TPUART_DEFS_H_ */
