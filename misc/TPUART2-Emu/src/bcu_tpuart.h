/*
 *  bcu_tpuart.h - Minimal BCU that turns the Selfbus library into a plain
 *                 KNX TP1 transceiver.
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License version 3 as
 *  published by the Free Software Foundation.
 */
#ifndef BCU_TPUART_H_
#define BCU_TPUART_H_

#include <cstdint>

#include <sblib/eib/bcu_base.h>
#include <sblib/eib/bus.h>
#include <sblib/eib/userRamBCU1.h>

/**
 * BCU for the TP-UART emulator.
 *
 * The transport layer is kept disabled, so @ref Bus::handleTelegram hands
 * every received telegram to the application instead of filtering it by our
 * own physical address. This is the same approach the Selfbus ft12 interface
 * uses.
 */
class BcuTpUart: public BcuBase
{
public:
    BcuTpUart();
    explicit BcuTpUart(UserRamBCU1* userRamBcu1);
    ~BcuTpUart() override = default;

    void begin();

    [[nodiscard]] bool applicationRunning() const override { return enabled; }
    uint8_t& layerStatus() override;

    /**
     * Enable or disable the link layer.
     *
     * With a disabled link layer the library stops acknowledging received
     * frames on the bus. Used for bus monitor mode and for the busy state.
     *
     * @param active true to acknowledge received frames, false to stay silent
     */
    void setLinkLayerActive(bool active);

    /**
     * @return true if received frames are acknowledged on the bus
     */
    [[nodiscard]] bool linkLayerActive() const;

protected:
    bool processApci(ApciCommand apciCmd, uint8_t* telegram, uint8_t telLength,
                     uint8_t* sendBuffer) override;
    bool processGroupAddressTelegram(ApciCommand apciCmd, uint16_t groupAddress,
                                     uint8_t* telegram, uint8_t telLength) override;
    bool processBroadCastTelegram(ApciCommand apciCmd, uint8_t* telegram,
                                  uint8_t telLength) override;
};

#endif /* BCU_TPUART_H_ */
