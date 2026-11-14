#ifndef AVRUPDI_DEVICE_H
#define AVRUPDI_DEVICE_H
#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace avrprog {
struct Fuse {
	const char *name;
	uint8_t offset;
	uint8_t reserved;
	bool writable = true;
};
struct Device {
	const char *name;
	const char *family;
	char nvmVersion;
	unsigned addressBytes;
	std::array<uint8_t, 3> signature;
	uint32_t signatureBase, flashBase;
	unsigned flashSize, pageSize, eepromSize;
	uint32_t fuseBase, lockBase, unlocked, locked;
	unsigned lockSize;
	std::vector<Fuse> fuses;
	unsigned pageCount() const { return flashSize / pageSize; }
	bool isDu() const { return nvmVersion == '4'; }
	bool isDd() const { return nvmVersion == '2'; }
	bool directWrite() const { return isDu() || isDd(); }
	unsigned sibSize() const { return isDd() ? 16 : 32; }
	uint32_t nvmStatus() const { return isDu() ? 0x1006 : 0x1002; }
	uint8_t nvmErrorMask() const { return directWrite() ? 0x70 : 4; }
};
inline const Device ATtiny202 {
	"ATtiny202", "tinyAVR", '0', 2, {0x1e, 0x91, 0x23},
	0x1100, 0x8000, 2048, 64, 64, 0x1280, 0x128a, 0xc5, 0x3a, 1,
	{{"WDTCFG", 0, 0}, {"BODCFG", 1, 0}, {"OSCCFG", 2, 0x7c},
	 {"SYSCFG0", 5, 0x32}, {"SYSCFG1", 6, 0xf8}, {"APPEND", 7, 0}, {"BOOTEND", 8, 0}}
};
// Microchip avr64du32.py (pymcuprog), AVR64DU28/32 DS40002548A,
// and ioavr64du32.h: P:4, UPDI 24-bit, byte addresses.
inline const Device AVR64DU32 {
	"AVR64DU32", "AVR", '4', 3, {0x1e, 0x96, 0x21},
	0x1080, 0x800000, 65536, 512, 256, 0x1050, 0x1040, 0x5cc5c55c, 0xa33a3aa3, 4,
	{{"WDTCFG", 0, 0}, {"BODCFG", 1, 0}, {"OSCCFG", 2, 0xf8},
	 {"SYSCFG0", 5, 0x04}, {"SYSCFG1", 6, 0xf0}, {"CODESIZE", 7, 0}, {"BOOTSIZE", 8, 0},
	 {"PDICFGL", 10, 0, false}, {"PDICFGH", 11, 0, false}}
};
// Microchip avr64dd28.py / avr64dd32.py and DS40002315C:
// P:2, 24-bit addresses, no page buffer, 16-byte SIB.
inline const Device AVR64DD28 {
	"AVR64DD28", "AVR", '2', 3, {0x1e, 0x96, 0x1b},
	0x1100, 0x800000, 65536, 512, 256, 0x1050, 0x1040, 0x5cc5c55c, 0xa33a3aa3, 4,
	{{"WDTCFG", 0, 0}, {"BODCFG", 1, 0}, {"OSCCFG", 2, 0xf0},
	 {"SYSCFG0", 5, 0x06}, {"SYSCFG1", 6, 0xe0}, {"CODESIZE", 7, 0}, {"BOOTSIZE", 8, 0}}
};
inline const Device AVR64DD32 {
	"AVR64DD32", "AVR", '2', 3, {0x1e, 0x96, 0x1a},
	0x1100, 0x800000, 65536, 512, 256, 0x1050, 0x1040, 0x5cc5c55c, 0xa33a3aa3, 4,
	AVR64DD28.fuses
};
inline const Device &findDevice(std::string name)
{
	for (auto &c : name) if (c >= 'a' && c <= 'z') c -= 'a' - 'A';
	if (name == "ATTINY202") return ATtiny202;
	if (name == "AVR64DU32") return AVR64DU32;
	if (name == "AVR64DD28") return AVR64DD28;
	if (name == "AVR64DD32") return AVR64DD32;
	throw std::runtime_error("unknown device: " + name + " (ATtiny202, AVR64DU32, AVR64DD28 or AVR64DD32)");
}
}
#endif
