#ifndef AVRUPDI_NVM_H
#define AVRUPDI_NVM_H
#include "image.h"
#include <chrono>
#include <thread>

namespace avrprog {
// Link supplies acknowledged byte/word stores and byte/block loads. All addresses are bytes.
template<class Link> class Nvm {
public:
	explicit Nvm(Link &link, const Device &device) : link_(link), device_(device) {}
	void ready()
	{
		const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(2);
		do {
			const auto status = link_.readByte(device_.isDu() ? 0x1006 : 0x1002);
			if (status & (device_.isDu() ? 0x70 : 4)) throw std::runtime_error("NVM write error; operation stopped");
			if (!(status & 3)) return;
			std::this_thread::sleep_for(std::chrono::milliseconds(2));
		} while (std::chrono::steady_clock::now() < end);
		throw std::runtime_error("NVM busy timeout; result uncertain, do not retry blindly");
	}
	Page readPage(unsigned page)
	{
		if (page >= device_.pageCount()) throw std::runtime_error("invalid flash page");
		ready();
		Page data(device_.pageSize);
		const auto bytes = link_.readBlock(device_.flashBase + page * device_.pageSize, device_.pageSize);
		std::copy(bytes.begin(), bytes.end(), data.begin());
		return data;
	}
	void write(const Image &image)
	{
		std::vector<unsigned> pending;
		// Complete preflight before the first page-buffer or NVM write.
		for (const auto &[p, data] : image) {
			if (data.size() != device_.pageSize) throw std::runtime_error("invalid page data size");
			const auto old = readPage(p);
			if (old == data) continue;
			if (!std::all_of(old.begin(), old.end(), [](uint8_t b) { return b == 0xff; }))
				throw std::runtime_error("flash page " + std::to_string(p) + " is neither blank nor identical; erase explicitly");
			pending.push_back(p);
		}
		for (auto p : pending) {
			const auto &data = image.at(p);
			if (device_.isDu()) {
				withCommand(2, [&]() {
					for (unsigned i = 0; i < device_.pageSize; i += 2) {
						link_.writeWord(device_.flashBase + p * device_.pageSize + i,
							data[i] | (uint16_t(data[i + 1]) << 8));
						ready();
					}
				});
			} else {
				ready(); command(4); ready();
				for (unsigned i = 0; i < device_.pageSize; i += 2)
					link_.writeWord(device_.flashBase + p * device_.pageSize + i,
						data[i] | (uint16_t(data[i + 1]) << 8));
				command(1); ready();
			}
			if (readPage(p) != data) throw std::runtime_error("flash verify mismatch at page " + std::to_string(p));
		}
	}
	uint8_t readFuse(uint8_t offset) { ready(); return link_.readByte(device_.fuseBase + offset); }
	void writeFuse(const Fuse &f, uint8_t value)
	{
		validateFuse(device_, f, value);
		if (readFuse(f.offset) == value) return;
		programFuse(f.offset, value);
		if (readFuse(f.offset) != value) throw std::runtime_error("fuse verify mismatch");
	}
	uint32_t readLock()
	{
		ready();
		uint32_t value = 0;
		for (unsigned i = 0; i < device_.lockSize; ++i)
			value |= uint32_t(link_.readByte(device_.lockBase + i)) << (8 * i);
		return value;
	}
	void lock()
	{
		if (readLock() != device_.unlocked) throw std::runtime_error("device is already locked");
		if (device_.isDu()) {
			// PDID + LOCK would disable even chip erase through UPDI.
			if (readFuse(10) != 3 || readFuse(11) != 0)
				throw std::runtime_error("refusing lock with non-default PDICFG; UPDI recovery may be disabled");
			withCommand(0x13, [&]() {
				for (unsigned i = 0; i < device_.lockSize; ++i) {
					link_.writeByte(device_.lockBase + i, static_cast<uint8_t>(device_.locked >> (8 * i)));
					ready();
				}
			});
		} else programFuse(10, 0x3a);
		if (readLock() != device_.locked) throw std::runtime_error("lock verify mismatch; lock state uncertain");
	}
	void erase(bool allowEeprom, bool verifyFlash = false, bool allowBootrow = false)
	{
		const auto cfg = readFuse(5);
		if (!(cfg & 1) && !allowEeprom)
			throw std::runtime_error("EESAVE=0: chip erase also erases EEPROM; require --allow-eeprom-erase");
		if (device_.isDu() && !(cfg & 2) && !allowBootrow)
			throw std::runtime_error("BROWSAVE=0: chip erase also erases Boot Row; require --allow-bootrow-erase");
		const auto eeprom = link_.readBlock(0x1400, device_.eepromSize);
		const auto bootrow = device_.isDu() ? link_.readBlock(0x1100, 256) : std::vector<uint8_t>{};
		if (device_.isDu()) withCommand(0x20, []() {});
		else { ready(); command(5); ready(); }
		if (verifyFlash) {
			for (unsigned p = 0; p < device_.pageCount(); ++p) {
				auto data = readPage(p);
				if (!std::all_of(data.begin(), data.end(), [](uint8_t b) { return b == 255; }))
					throw std::runtime_error("chip erase flash verification failed");
			}
		}
		const auto after = link_.readBlock(0x1400, eeprom.size());
		for (unsigned i = 0; i < eeprom.size(); ++i)
			if (after[i] != ((cfg & 1) ? eeprom[i] : 0xff))
				throw std::runtime_error("chip erase EEPROM verification failed");
		if (device_.isDu()) {
			const auto afterBootrow = link_.readBlock(0x1100, bootrow.size());
			for (unsigned i = 0; i < bootrow.size(); ++i)
				if (afterBootrow[i] != ((cfg & 2) ? bootrow[i] : 0xff))
					throw std::runtime_error("chip erase Boot Row verification failed");
		}
		if (readLock() != device_.unlocked) throw std::runtime_error("chip erase lock verification failed");
	}
private:
	void command(uint8_t command) { link_.writeByte(0x1000, command); }
	void programFuse(uint8_t offset, uint8_t value)
	{
		if (device_.isDu()) {
			withCommand(0x13, [&]() { link_.writeByte(device_.fuseBase + offset, value); });
			return;
		}
		ready();
		link_.writeByte(0x1008, 0x80 + offset);
		link_.writeByte(0x1009, 0x12);
		link_.writeByte(0x1006, value);
		command(7); ready();
	}
	template<class Operation> void withCommand(uint8_t cmd, Operation operation)
	{
		ready();
		try { command(cmd); operation(); ready(); }
		catch (...) {
			// Do not retry the write. Try only to leave the write-enable mode.
			try { command(0); } catch (...) {}
			throw;
		}
		command(0);
	}
	Link &link_;
	const Device &device_;
};
}
#endif
