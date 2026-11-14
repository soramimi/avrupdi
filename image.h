#ifndef AVRUPDI_IMAGE_H
#define AVRUPDI_IMAGE_H
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace tiny202 {
constexpr unsigned FlashSize = 2048, PageSize = 64, PageCount = FlashSize / PageSize;
using Page = std::array<uint8_t, PageSize>;
using Image = std::map<unsigned, Page>;

inline unsigned number(const std::string &s, unsigned max, int base = 10)
{
	if (s.empty() || s[0] == '-' || s[0] == '+' || s[0] == ' ')
		throw std::runtime_error("invalid number: " + s);
	if (s.size() > 2 && s.substr(0, 2) == "0x") base = 16;
	size_t start = s.size() > 2 && s.substr(0, 2) == "0x" ? 2 : 0;
	for (size_t i = start; i < s.size(); ++i) {
		const char c = s[i];
		if (!(c >= '0' && c <= '9') && !(base == 16 && ((c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))))
			throw std::runtime_error("invalid number: " + s);
	}
	size_t used = 0;
	const auto n = std::stoul(s, &used, base);
	if (used != s.size() || n > max) throw std::runtime_error("number out of range: " + s);
	return static_cast<unsigned>(n);
}

inline Image readHex(const std::string &path)
{
	std::ifstream in(path);
	if (!in) throw std::runtime_error("cannot open HEX input: " + path);
	Image image;
	std::array<bool, FlashSize> seen {};
	uint32_t base = 0;
	bool eof = false;
	std::string line;
	unsigned lineno = 0;
	while (std::getline(in, line)) {
		++lineno;
		if (!line.empty() && line.back() == '\r') line.pop_back();
		if (line.empty()) continue;
		auto fail = [&]() { throw std::runtime_error("invalid HEX at line " + std::to_string(lineno)); };
		if (eof || line.size() < 11 || line[0] != ':' || line.size() % 2 != 1) fail();
		std::vector<uint8_t> r;
		unsigned sum = 0;
		for (size_t i = 1; i < line.size(); i += 2) {
			auto digit = [&](char c) -> unsigned {
				if (c >= '0' && c <= '9') return c - '0';
				if (c >= 'A' && c <= 'F') return c - 'A' + 10;
				if (c >= 'a' && c <= 'f') return c - 'a' + 10;
				fail(); return 0;
			};
			const auto b = digit(line[i]) * 16 + digit(line[i + 1]);
			r.push_back(b); sum += b;
		}
		if (r.size() != static_cast<size_t>(r[0]) + 5 || (sum & 255)) fail();
		const unsigned address = r[1] * 256 + r[2], count = r[0];
		if (r[3] == 0) {
			if (address + count > 65536 || uint64_t(base) + address + count > FlashSize) fail();
			for (unsigned i = 0; i < count; ++i) {
				unsigned a = base + address + i;
				if (seen[a]) fail(); // Reject even identical overlapping records.
				seen[a] = true;
				unsigned p = a / PageSize;
				if (!image.count(p)) { Page page; page.fill(0xff); image[p] = page; }
				image[p][a % PageSize] = r[4 + i];
			}
		} else if (r[3] == 1) {
			if (count || address) fail();
			eof = true;
		} else if (r[3] == 2 || r[3] == 4) {
			if (count != 2 || address) fail();
			base = uint32_t(r[4] * 256 + r[5]) << (r[3] == 2 ? 4 : 16);
		} else fail();
	}
	if (in.bad() || !eof || image.empty()) throw std::runtime_error("HEX requires data and EOF");
	return image;
}

inline std::string encodeHex(const Image &image)
{
	std::string out;
	for (const auto &[p, data] : image) {
		if (p >= PageCount) throw std::runtime_error("invalid page");
		for (unsigned i = 0; i < PageSize; i += 16) {
			unsigned a = p * PageSize + i, sum = 16 + (a >> 8) + (a & 255);
			char b[16];
			std::snprintf(b, sizeof b, ":10%04X00", a); out += b;
			for (unsigned j = 0; j < 16; ++j) {
				std::snprintf(b, sizeof b, "%02X", data[i + j]); out += b; sum += data[i + j];
			}
			std::snprintf(b, sizeof b, "%02X\n", (-sum) & 255); out += b;
		}
	}
	return out + ":00000001FF\n";
}

struct Fuse { const char *name; uint8_t offset; uint8_t reserved; };
inline constexpr Fuse Fuses[] = {
	{"WDTCFG", 0, 0}, {"BODCFG", 1, 0}, {"OSCCFG", 2, 0x7c},
	{"SYSCFG0", 5, 0x32}, {"SYSCFG1", 6, 0xf8}, {"APPEND", 7, 0}, {"BOOTEND", 8, 0}
};
inline const Fuse &findFuse(const std::string &name)
{
	for (const auto &f : Fuses) if (name == f.name) return f;
	throw std::runtime_error("unknown fuse name: " + name);
}
inline void validateFuse(const Fuse &f, uint8_t v)
{
	if ((v & f.reserved) != f.reserved) throw std::runtime_error("reserved fuse bits must be 1");
	if (f.offset == 0 && ((v & 15) > 11 || (v >> 4) > 11)) throw std::runtime_error("reserved watchdog value");
	if (f.offset == 1 && ((v & 3) == 3 || ((v >> 5) != 0 && (v >> 5) != 2 && (v >> 5) != 7)))
		throw std::runtime_error("reserved BOD value");
	if (f.offset == 2 && (v & 3) != 1 && (v & 3) != 2) throw std::runtime_error("reserved oscillator frequency");
	if (f.offset == 5 && (v & 12) != 4) throw std::runtime_error("refusing to disable UPDI pin");
	if ((f.offset == 7 || f.offset == 8) && v > FlashSize / 256) throw std::runtime_error("section exceeds ATtiny202 flash");
}
}
#endif
