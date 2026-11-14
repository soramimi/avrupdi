#ifndef AVRUPDI_NVM_H
#define AVRUPDI_NVM_H
#include "image.h"
#include <chrono>
#include <thread>

namespace tiny202 {
// Link supplies acknowledged byte/word stores and byte loads. All addresses are bytes.
template<class Link> class Nvm {
public:
	explicit Nvm(Link &link) : link_(link) {}
	void ready()
	{
		const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(2);
		do {
			const auto status = link_.readByte(0x1002);
			if (status & 4) throw std::runtime_error("NVM write error; operation stopped");
			if (!(status & 3)) return;
			std::this_thread::sleep_for(std::chrono::milliseconds(2));
		} while (std::chrono::steady_clock::now() < end);
		throw std::runtime_error("NVM busy timeout; result uncertain, do not retry blindly");
	}
	Page readPage(unsigned page)
	{
		if (page >= PageCount) throw std::runtime_error("invalid flash page");
		ready();
		Page data;
		for (unsigned i = 0; i < PageSize; ++i) data[i] = link_.readByte(0x8000 + page * PageSize + i);
		return data;
	}
	void write(const Image &image)
	{
		std::vector<unsigned> pending;
		// Complete preflight before the first page-buffer or NVM write.
		for (const auto &[p, data] : image) {
			const auto old = readPage(p);
			if (old == data) continue;
			if (!std::all_of(old.begin(), old.end(), [](uint8_t b) { return b == 0xff; }))
				throw std::runtime_error("flash page " + std::to_string(p) + " is neither blank nor identical; erase explicitly");
			pending.push_back(p);
		}
		for (auto p : pending) {
			const auto &data = image.at(p);
			ready(); command(4); ready();
			for (unsigned i = 0; i < PageSize; i += 2)
				link_.writeWord(0x8000 + p * PageSize + i, data[i] | (uint16_t(data[i + 1]) << 8));
			command(1); ready();
			if (readPage(p) != data) throw std::runtime_error("flash verify mismatch at page " + std::to_string(p));
		}
	}
	uint8_t readFuse(uint8_t offset) { ready(); return link_.readByte(0x1280 + offset); }
	void writeFuse(const Fuse &f, uint8_t value)
	{
		validateFuse(f, value);
		if (readFuse(f.offset) == value) return;
		programFuse(f.offset, value);
		if (readFuse(f.offset) != value) throw std::runtime_error("fuse verify mismatch");
	}
	void lock()
	{
		if (readFuse(10) != 0xc5) throw std::runtime_error("device is already locked");
		programFuse(10, 0x3a);
		if (readFuse(10) != 0x3a) throw std::runtime_error("lock verify mismatch; lock state uncertain");
	}
	void erase(bool allowEeprom)
	{
		const auto cfg = readFuse(5);
		if (!(cfg & 1) && !allowEeprom)
			throw std::runtime_error("EESAVE=0: chip erase also erases EEPROM; require --allow-eeprom-erase");
		std::array<uint8_t, 64> eeprom;
		for (unsigned i = 0; i < eeprom.size(); ++i) eeprom[i] = link_.readByte(0x1400 + i);
		ready(); command(5); ready();
		for (unsigned p = 0; p < PageCount; ++p) {
			auto data = readPage(p);
			if (!std::all_of(data.begin(), data.end(), [](uint8_t b) { return b == 255; }))
				throw std::runtime_error("chip erase flash verification failed");
		}
		for (unsigned i = 0; i < eeprom.size(); ++i)
			if (link_.readByte(0x1400 + i) != ((cfg & 1) ? eeprom[i] : 0xff))
				throw std::runtime_error("chip erase EEPROM verification failed");
		if (readFuse(10) != 0xc5) throw std::runtime_error("chip erase lock verification failed");
	}
private:
	void command(uint8_t command) { link_.writeByte(0x1000, command); }
	void programFuse(uint8_t offset, uint8_t value)
	{
		ready();
		link_.writeByte(0x1008, 0x80 + offset);
		link_.writeByte(0x1009, 0x12);
		link_.writeByte(0x1006, value);
		command(7); ready();
	}
	Link &link_;
};
}
#endif
