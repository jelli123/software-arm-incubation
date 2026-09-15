/*
 *  tpuart_emulator.cpp - KNX TP-UART 2 host protocol emulation on top of sblib.
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License version 3 as
 *  published by the Free Software Foundation.
 */

#include <sblib/bits.h>
#include <sblib/digital_pin.h>
#include <sblib/serial.h>
#include <sblib/timer.h>
#include <sblib/eib/bus_const.h>
#include <sblib/eib/knx_lpdu.h>

#include <cstring>

#include "tpuart_defs.h"
#include "tpuart_emulator.h"

TpUartEmulator::TpUartEmulator(BcuTpUart& bcuTpUart) :
    bcu(bcuTpUart),
    cmdByte(0),
    cmdData{},
    cmdDataLen(0),
    cmdDataExpected(0),
    assembleBuffer{},
    txOffset(0),
    txFrame{},
    txPending(false),
    txSuppressCon(false),
    txStartTime(0),
    txQueue(TPUART_TX_QUEUE_SIZE),
    lastTxByteTime(0),
    busMonitorMode(false),
    busyMode(false),
    stopMode(false),
    errorFlags(0)
{
}

void TpUartEmulator::begin()
{
    lastTxByteTime = millis();
    bcu.setLinkLayerActive(true);
}

void TpUartEmulator::loop()
{
    pollHost();
    pollKnxTransmit();
    pollKnxReceive();
    drainQueue();
    pollLeds();
}

/*
 * ---------------------------------------------------------------------------
 * Host -> controller
 * ---------------------------------------------------------------------------
 */

uint8_t TpUartEmulator::hostServiceDataLength(const uint8_t cmd)
{
    switch (cmd)
    {
    case U_SET_ADDRESS_REQ:     // 0x28 + addrHigh + addrLow
    case U_SET_ADDRESS_REQ_ALT: // 0xF1 + addrHigh + addrLow
        return 2;

    // Repetition counter. The OpenKNX stack sends this whenever the configured
    // NACK/BUSY repetition count differs from the default, but only when it is
    // NOT built for the NCN512x (there the same setting uses 0xF2).
    case U_MXRSTCNT_REQ:
    case U_SET_REPETITION_REQ:
        return 1;

    default:
        if ((cmd >= U_L_DATA_END_REQ) && (cmd <= U_L_DATA_START_CONT_REQ_MAX))
        {
            return 1; // U_L_DataEnd and U_L_DataStart/Cont, followed by the frame octet
        }
        if ((cmd >= U_INT_REG_WR_REQ) && (cmd <= U_INT_REG_WR_REQ_MAX))
        {
            return 1; // NCN512x register write
        }
        return 0;
    }
}

void TpUartEmulator::processHostByte(const uint8_t data)
{
    if (cmdDataExpected != 0)
    {
        cmdData[cmdDataLen++] = data;
        if (cmdDataLen < cmdDataExpected)
        {
            return;
        }
        cmdDataExpected = 0;
    }
    else
    {
        cmdByte = data;
        cmdDataLen = 0;
        cmdDataExpected = hostServiceDataLength(data);
        if (cmdDataExpected != 0)
        {
            return;
        }
    }

    handleHostService(cmdByte, cmdData);
}

void TpUartEmulator::handleHostService(const uint8_t cmd, const uint8_t* data)
{
    // Frame transmission services are the most frequent ones, handle them first.
    if ((cmd >= U_L_DATA_END_REQ) && (cmd <= U_L_DATA_START_CONT_REQ_MAX))
    {
        const auto index = static_cast<uint16_t>(txOffset * U_L_DATA_OFFSET_UNIT + (cmd & U_L_DATA_INDEX_MASK));
        if (cmd >= U_L_DATA_START_CONT_REQ)
        {
            handleDataOctet(index, data[0]);
        }
        else
        {
            handleFrameEnd(index, data[0]);
        }
        return;
    }

    if ((cmd >= U_L_DATA_OFFSET_REQ) && (cmd <= U_L_DATA_OFFSET_REQ_MAX))
    {
        txOffset = cmd & U_L_DATA_OFFSET_MASK;
        return;
    }

    if ((cmd >= U_ACK_INFORMATION_REQ) && (cmd <= U_ACK_INFORMATION_REQ_MAX))
    {
        // The acknowledge decision cannot be delegated to the host: the KNX
        // acknowledge slot opens 15 bit times after the last frame octet, while
        // sblib only reports a telegram once it is completely received. The
        // library therefore acknowledges autonomously, see README.md.
        return;
    }

    switch (cmd)
    {
    case U_RESET_REQ:
        handleReset();
        break;

    case U_STATE_REQ:
        handleStateRequest();
        break;

    case U_SET_BUSY_REQ:
        busyMode = true;
        bcu.setLinkLayerActive(false);
        break;

    case U_QUIT_BUSY_REQ:
        busyMode = false;
        if (!busMonitorMode)
        {
            bcu.setLinkLayerActive(true);
        }
        break;

    case U_BUSMON_REQ:
        // In bus monitor mode nothing may be acknowledged on the bus.
        busMonitorMode = true;
        bcu.setLinkLayerActive(false);
        break;

    case U_SYSTEM_STATE_REQ:
        queueByte(U_SYSTEM_STAT_IND);
        queueByte(static_cast<uint8_t>(stopMode));
        break;

    case U_STOP_MODE_REQ:
        if (!stopMode)
        {
            bcu.bus->pause(true);
            stopMode = true;
        }
        queueByte(U_STOP_MODE_IND);
        break;

    case U_EXIT_STOP_MODE_REQ:
        if (stopMode)
        {
            bcu.bus->resume();
            stopMode = false;
        }
        break;

    case U_SET_ADDRESS_REQ:
    case U_SET_ADDRESS_REQ_ALT:
        bcu.setOwnAddress(makeWord(data[0], data[1]));
        break;

    default:
        // U_ProductId, U_Configure, U_IntRegRd/Wr, U_MxRstCnt, U_SetRepetition
        // and anything unknown are silently accepted. Their data octets, if any,
        // have already been consumed by hostServiceDataLength().
        break;
    }
}

void TpUartEmulator::handleReset()
{
    if (stopMode)
    {
        bcu.bus->resume();
        stopMode = false;
    }

    cmdDataExpected = 0;
    cmdDataLen = 0;
    txOffset = 0;
    busMonitorMode = false;
    busyMode = false;
    errorFlags = 0;
    bcu.setLinkLayerActive(true);

    // Drop everything that is still queued towards the host.
    txQueue.clear();
    bcu.bus->discardReceivedTelegram();

    if (txPending)
    {
        // A frame may still be on its way out. Keep the buffer reserved, but
        // do not report its confirmation to the host after the reset.
        txSuppressCon = true;
    }

    queueByte(U_RESET_IND);
}

void TpUartEmulator::handleStateRequest()
{
    queueByte(static_cast<uint8_t>(U_STATE_IND | errorFlags));
    errorFlags = 0;
}

void TpUartEmulator::handleDataOctet(const uint16_t index, const uint8_t data)
{
    if (index >= TPUART_MAX_FRAME_SIZE)
    {
        errorFlags |= TPUART_PROTOCOL_ERROR;
        return;
    }
    assembleBuffer[index] = data;
}

void TpUartEmulator::handleFrameEnd(const uint16_t index, const uint8_t data)
{
    txOffset = 0;

    if (index >= TPUART_MAX_FRAME_SIZE)
    {
        rejectFrame();
        return;
    }

    assembleBuffer[index] = data;
    submitFrame(static_cast<uint16_t>(index + 1));
}

bool TpUartEmulator::isSendableFrame(const uint16_t length) const
{
    if ((length < LPDU_STD_OVERHEAD) || (length > TPUART_MAX_FRAME_SIZE))
    {
        return false;
    }

    // sblib's Bus state machine implements standard frames only. Also check the
    // fixed bits of the control byte, Bus::sendTelegram() does not validate them.
    if ((frameType(assembleBuffer) != FRAME_STANDARD) ||
        ((assembleBuffer[0] & VALID_DATA_FRAME_TYPE_MASK) != VALID_DATA_FRAME_TYPE_VALUE))
    {
        return false;
    }

    return length == (assembleBuffer[LPDU_STD_LENGTH_OCTET] & LPDU_STD_LENGTH_MASK) + LPDU_STD_OVERHEAD;
}

void TpUartEmulator::rejectFrame()
{
    errorFlags |= TPUART_PROTOCOL_ERROR;
    queueByte(L_DATA_CON);
}

void TpUartEmulator::submitFrame(const uint16_t length)
{
    // Reject invalid frames, frames sent before the previous confirmation and
    // frames sent while the transceiver is detached from the bus.
    if (!isSendableFrame(length) || txPending || stopMode)
    {
        rejectFrame();
        return;
    }

    memcpy(txFrame, assembleBuffer, length);

    // Bus::prepareTelegram() overwrites the sender address with bcu->ownAddress().
    // A transceiver has to put the frame on the bus verbatim, so adopt the source
    // address of this frame first. This is what makes tunneling with several
    // individual addresses work.
    bcu.setOwnAddress(makeWord(txFrame[1], txFrame[2]));

    // The last octet is the checksum, sblib recalculates and appends it.
    bcu.bus->sendTelegram(txFrame, static_cast<uint16_t>(length - 1));
    txPending = true;
    txSuppressCon = false;
    txStartTime = millis();
}

void TpUartEmulator::pollHost()
{
    int16_t data;
    while ((data = serial.read()) >= 0)
    {
        hostRxLed.start(LED_BLINK_MS);
        digitalWrite(LED_SERIAL_RX, LED_ON);
        processHostByte(static_cast<uint8_t>(data));
    }
}

/*
 * ---------------------------------------------------------------------------
 * KNX bus
 * ---------------------------------------------------------------------------
 */

void TpUartEmulator::pollKnxReceive()
{
    if (!bcu.bus->telegramReceived())
    {
        return;
    }

    const auto length = static_cast<uint16_t>(bcu.bus->telegramLen);

    if ((length < LPDU_STD_OVERHEAD) || (length > TPUART_MAX_FRAME_SIZE))
    {
        errorFlags |= TPUART_RECEIVE_ERROR;
        bcu.bus->discardReceivedTelegram();
        return;
    }

    if (!queueFree(length))
    {
        // Apply back pressure: as long as the telegram is not discarded, sblib
        // reports the receive buffer as busy and stops acknowledging.
        return;
    }

    queueBytes(bcu.bus->telegram, length);
    bcu.bus->discardReceivedTelegram();

    knxRxLed.start(LED_BLINK_MS);
    digitalWrite(LED_KNX_RX, LED_ON);
}

void TpUartEmulator::pollKnxTransmit()
{
    if (!txPending)
    {
        return;
    }

    if (!bcu.bus->sendingFrame())
    {
        txPending = false;
        if (!txSuppressCon)
        {
            // sblib does not expose the transmission result, and it already
            // repeats a frame on NACK and BUSY. Report a positive confirmation.
            queueByte(static_cast<uint8_t>(L_DATA_CON | L_DATA_CON_SUCCESS));
        }
        txSuppressCon = false;
        return;
    }

    if (static_cast<uint32_t>(millis() - txStartTime) >= TPUART_TX_CONFIRM_TIMEOUT_MS)
    {
        txPending = false;
        errorFlags |= TPUART_TRANSMIT_ERROR;
        if (!txSuppressCon)
        {
            queueByte(L_DATA_CON);
        }
        txSuppressCon = false;
    }
}

void TpUartEmulator::pollLeds()
{
    if (knxRxLed.expired())
    {
        digitalWrite(LED_KNX_RX, LED_OFF);
    }
    if (hostRxLed.expired())
    {
        digitalWrite(LED_SERIAL_RX, LED_OFF);
    }
}

/*
 * ---------------------------------------------------------------------------
 * Controller -> host, paced output queue
 * ---------------------------------------------------------------------------
 */

bool TpUartEmulator::queueFree(const uint16_t count) const
{
    // A RingBuffer holds at most getBufferSize() - 1 octets.
    return (txQueue.getBufferSize() - 1u - txQueue.available()) >= count;
}

void TpUartEmulator::queueByte(const uint8_t data)
{
    if (!txQueue.push(data))
    {
        errorFlags |= TPUART_PROTOCOL_ERROR;
    }
}

void TpUartEmulator::queueBytes(const uint8_t* data, const uint16_t length)
{
    for (uint16_t i = 0; i < length; i++)
    {
        queueByte(data[i]);
    }
}

void TpUartEmulator::drainQueue()
{
    while (!txQueue.empty())
    {
#if (TPUART_TX_PACING_MS > 0)
        if (static_cast<uint32_t>(millis() - lastTxByteTime) < TPUART_TX_PACING_MS)
        {
            return;
        }
#endif
        if (serial.write(static_cast<uint8_t>(txQueue.peek())) != 1)
        {
            return; // serial write buffer is full, try again next round
        }
        static_cast<void>(txQueue.pop());
        lastTxByteTime = millis();

#if (TPUART_TX_PACING_MS > 0)
        return; // one octet per pacing interval
#endif
    }
}
