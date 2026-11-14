#include "nvm.h"
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <termios.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
using Bytes = std::vector<uint8_t>;
using namespace std::chrono_literals;

std::runtime_error systemError(const char *operation)
{
	return std::runtime_error(std::string(operation) + ": " + std::strerror(errno));
}

class SerialPort {
public:
	SerialPort(const char *path, bool verbose)
		: verbose_(verbose)
	{
		fd_ = ::open(path, O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
		if (fd_ < 0) throw systemError("open serial port");
		try {
			if (tcgetattr(fd_, &original_) < 0) throw systemError("tcgetattr");
			saved_ = true;
			configure(B115200, true);
			discardInput();
		} catch (...) {
			close();
			throw;
		}
	}
	~SerialPort() { close(); }
	SerialPort(const SerialPort &) = delete;
	SerialPort &operator=(const SerialPort &) = delete;

	void configure(speed_t speed, bool twoStops)
	{
		termios settings = original_;
		cfmakeraw(&settings);
		settings.c_cflag &= ~(CSIZE | PARODD | CSTOPB | CRTSCTS | HUPCL);
		settings.c_cflag |= CS8 | PARENB | CLOCAL | CREAD;
		if (twoStops) settings.c_cflag |= CSTOPB;
		settings.c_iflag &= ~(IGNPAR | PARMRK);
		settings.c_iflag |= INPCK;
		settings.c_cc[VMIN] = 0;
		settings.c_cc[VTIME] = 0;
		if (cfsetispeed(&settings, speed) < 0 || cfsetospeed(&settings, speed) < 0)
			throw systemError("set baud rate");
		if (tcsetattr(fd_, TCSANOW, &settings) < 0) throw systemError("tcsetattr");
	}
	void discardInput()
	{
		if (tcflush(fd_, TCIFLUSH) < 0) throw systemError("tcflush");
	}
	// RX receives TX as an echo. Never flush here: the target response may
	// already be queued immediately after the echo.
	void send(const Bytes &bytes)
	{
		log("TX", bytes);
		const auto deadline = Clock::now() + 1000ms;
		size_t offset = 0;
		while (offset < bytes.size()) {
			wait(POLLOUT, deadline, "write");
			const auto n = ::write(fd_, bytes.data() + offset, bytes.size() - offset);
			if (n < 0) {
				if (errno == EINTR || errno == EAGAIN) continue;
				throw systemError("write");
			}
			if (n == 0) throw std::runtime_error("serial write returned zero");
			offset += static_cast<size_t>(n);
		}
		const auto echo = receive(bytes.size(), deadline, "echo");
		log("ECHO", echo);
		if (echo != bytes) throw std::runtime_error("serial echo mismatch (check SerialUPDI wiring)");
	}
	Bytes receive(size_t size)
	{
		auto bytes = receive(size, Clock::now() + 1000ms, "response");
		log("RX", bytes);
		return bytes;
	}

private:
	void close() noexcept
	{
		if (fd_ >= 0) {
			if (saved_) tcsetattr(fd_, TCSANOW, &original_);
			::close(fd_);
			fd_ = -1;
		}
	}
	void wait(short events, Clock::time_point deadline, const char *phase)
	{
		for (;;) {
			const auto remaining = deadline - Clock::now();
			if (remaining <= Clock::duration::zero())
				throw std::runtime_error(std::string("timeout waiting for serial ") + phase);
			const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(remaining).count();
			pollfd pfd { fd_, events, 0 };
			const int result = ::poll(&pfd, 1, static_cast<int>(ms + 1));
			if (result < 0) {
				if (errno == EINTR) continue;
				throw systemError("poll");
			}
			if (result == 0) continue;
			if (pfd.revents & POLLNVAL) throw std::runtime_error("invalid serial descriptor");
			if (pfd.revents & events) return;
			if (pfd.revents & (POLLERR | POLLHUP))
				throw std::runtime_error("serial port disconnected");
		}
	}
	Bytes receive(size_t size, Clock::time_point deadline, const char *phase)
	{
		Bytes bytes(size);
		size_t offset = 0;
		while (offset < size) {
			wait(POLLIN, deadline, phase);
			const auto n = ::read(fd_, bytes.data() + offset, size - offset);
			if (n < 0) {
				if (errno == EINTR || errno == EAGAIN) continue;
				throw systemError("read");
			}
			if (n == 0) throw std::runtime_error("serial port reached end of input");
			offset += static_cast<size_t>(n);
		}
		return bytes;
	}
	void log(const char *label, const Bytes &bytes) const
	{
		if (!verbose_) return;
		std::fprintf(stderr, "%s:", label);
		for (auto value : bytes)
			std::fprintf(stderr, " %02X", value);
		std::fputc('\n', stderr);
	}
	int fd_ = -1;
	bool saved_ = false;
	bool verbose_;
	termios original_ { };
};

class Updi {
public:
	explicit Updi(SerialPort &port)
		: port_(port)
	{
	}
	void initialize()
	{
		try {
			port_.send({ 0x00 });
			std::this_thread::sleep_for(1ms);
			initializeLink();
		} catch (const std::exception &error) {
			std::fprintf(stderr, "Initial connection failed: %s; trying double BREAK\n", error.what());
			doubleBreak();
			initializeLink();
		}
		active_ = true;
		const auto status = readCs(0x0B);
		if (status & (0x20 | 0x0C)) {
			reset();
			initializeLink();
		}
		port_.send({ 0x55, 0xE6 });
		const auto sib = port_.receive(32);
		// The field at offset 8 is e.g. "P:0", not "N:0". Like avrdude,
		// select the NVM interface by the version following the colon.
		if (std::memcmp(sib.data(), "tinyAVR", 7) != 0 || sib[9] != ':' || sib[10] != '0') {
			std::string received;
			for (auto value : sib) {
				if (value >= 0x20 && value <= 0x7E && value != '\\' && value != '"')
					received += static_cast<char>(value);
				else {
					char escaped[5];
					std::snprintf(escaped, sizeof(escaped), "\\x%02X", value);
					received += escaped;
				}
			}
			throw std::runtime_error("unsupported SIB (expected tinyAVR NVM interface 0); received: \"" + received + "\"");
		}
		initializeLink();
	}
	void enterProgrammingMode()
	{
		if ((readCs(0x0B) & 0x09) == 0x08) return;
		writeCs(0x08, 0x59);
		port_.send({ 0x55, 0xE0 });
		port_.send({ 0x20, 0x67, 0x6F, 0x72, 0x50, 0x4D, 0x56, 0x4E });
		if (!(readCs(0x07) & 0x10)) throw std::runtime_error("NVM programming key rejected");
		reset();
		const auto deadline = Clock::now() + 500ms;
		uint8_t status = 0;
		do {
			status = readCs(0x0B);
			if ((status & 0x09) == 0x08) return;
			std::this_thread::sleep_for(2ms);
		} while (Clock::now() < deadline);
		if (status & 0x01)
			throw std::runtime_error("device is locked; signature cannot be read");
		throw std::runtime_error("timeout entering NVM programming mode");
	}
	std::array<uint8_t, 3> readSignature()
	{
		if (readCs(0x0B) & 0x01) throw std::runtime_error("device is locked");
		std::array<uint8_t, 3> signature { };
		for (size_t i = 0; i < signature.size(); ++i) {
			const uint16_t address = 0x1100 + i;
			port_.send({ 0x55, 0x04, static_cast<uint8_t>(address),
				static_cast<uint8_t>(address >> 8) });
			signature[i] = port_.receive(1)[0];
		}
		return signature;
	}
	uint8_t readByte(uint16_t address)
	{
		port_.send({0x55, 0x04, static_cast<uint8_t>(address), static_cast<uint8_t>(address >> 8)});
		return port_.receive(1)[0];
	}
	Bytes readBlock(uint16_t address, size_t size)
	{
		// Validate before transmitting; never wrap the 16-bit data-space address.
		if (size > 0x10000u - static_cast<size_t>(address))
			throw std::runtime_error("UPDI block read crosses 16-bit address boundary");
		Bytes result;
		result.reserve(size);
		while (result.size() < size) {
			const size_t count = std::min<size_t>(256, size - result.size());
			const auto current = static_cast<uint16_t>(address + result.size());
			// ST ptr (16-bit), low byte first. Re-establish ptr for each block.
			port_.send({0x55, 0x69, static_cast<uint8_t>(current), static_cast<uint8_t>(current >> 8)});
			if (port_.receive(1)[0] != 0x40) throw std::runtime_error("UPDI pointer ACK rejected");
			// REPEAT contains the number of additional executions (0..255).
			// No target ACK for REPEAT or LD; send() still checks every echo.
			if (count > 1) port_.send({0x55, 0xA0, static_cast<uint8_t>(count - 1)});
			port_.send({0x55, 0x24}); // LD byte, ptr post-increment
			const auto block = port_.receive(count); // One fixed deadline for the entire block.
			result.insert(result.end(), block.begin(), block.end());
		}
		return result;
	}
	void writeByte(uint16_t address, uint8_t value)
	{
		store(address, {value});
	}
	void writeWord(uint16_t address, uint16_t value)
	{
		store(address, {static_cast<uint8_t>(value), static_cast<uint8_t>(value >> 8)});
	}
	void verifyLocked()
	{
		reset();
		const auto deadline = Clock::now() + 500ms;
		do {
			if (readCs(0x0B) & 1) return;
			std::this_thread::sleep_for(2ms);
		} while (Clock::now() < deadline);
		throw std::runtime_error("lock activation verification failed; state uncertain");
	}
	void leaveProgrammingMode()
	{
		if (!active_) return;
		reset();
		writeCs(0x03, 0x0C);
		active_ = false;
	}

private:
	void store(uint16_t address, const Bytes &data)
	{
		port_.send({0x55, static_cast<uint8_t>(data.size() == 2 ? 0x45 : 0x44),
			static_cast<uint8_t>(address), static_cast<uint8_t>(address >> 8)});
		if (port_.receive(1)[0] != 0x40) throw std::runtime_error("UPDI address ACK rejected");
		port_.send(data);
		if (port_.receive(1)[0] != 0x40) throw std::runtime_error("UPDI data ACK rejected; write result uncertain");
	}
	void initializeLink()
	{
		writeCs(0x03, 0x08);
		writeCs(0x02, 0x80);
		if (readCs(0x00) == 0) throw std::runtime_error("UPDI STATUSA is zero");
	}
	void doubleBreak()
	{
		port_.configure(B300, false);
		std::this_thread::sleep_for(20ms);
		port_.discardInput();
		port_.send({ 0x00 });
		std::this_thread::sleep_for(100ms);
		port_.send({ 0x00 });
		std::this_thread::sleep_for(20ms);
		port_.configure(B115200, true);
		port_.discardInput();
	}
	uint8_t readCs(uint8_t address)
	{
		port_.send({ 0x55, static_cast<uint8_t>(0x80 | address) });
		return port_.receive(1)[0];
	}
	void writeCs(uint8_t address, uint8_t value)
	{
		port_.send({ 0x55, static_cast<uint8_t>(0xC0 | address), value });
	}
	void reset()
	{
		writeCs(0x08, 0x59);
		writeCs(0x08, 0x00);
	}
	SerialPort &port_;
	bool active_ = false;
};
} // namespace

// Do not overwrite an existing backup, device node or symlink.
void saveOutput(const std::string &path, const std::string &data)
{
	int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
	if (fd < 0) throw systemError("create output (must not already exist)");
	try {
		size_t n = 0;
		while (n < data.size()) {
			auto written = ::write(fd, data.data() + n, data.size() - n);
			if (written < 0 && errno == EINTR) continue;
			if (written <= 0) throw systemError("write output");
			n += written;
		}
		if (::fsync(fd) < 0) throw systemError("sync output");
	} catch (...) { ::close(fd); ::unlink(path.c_str()); throw; }
	if (::close(fd) < 0) throw systemError("close output");
}

int main(int argc, char **argv)
{
	try {
		std::string device = "/dev/ttyUSB0", action = "signature", path, fuse;
		bool verbose = false, deviceSet = false, selected = false;
		bool confirmErase = false, allowEeprom = false, confirmFuse = false, confirmLock = false;
		unsigned page = 0, value = 0;
		for (int i = 1; i < argc; ++i) {
			const std::string arg = argv[i];
			auto next = [&]() -> std::string {
				if (++i >= argc) throw std::runtime_error("missing value for " + arg);
				return argv[i];
			};
			if (arg == "-h" || arg == "--help") {
				std::printf("Usage: %s [-v] [PORT] [COMMAND] [CONFIRMATION]\n"
					"Default: read ATtiny202 signature. One command per invocation.\n"
					"  --read-page PAGE FILE     64-byte binary (PAGE 0..31)\n"
					"  --write-page PAGE FILE    blank or identical page only\n"
					"  --read-hex FILE           entire 2048-byte flash\n"
					"  --write-hex FILE          preflight all selected pages, FF padding\n"
					"  --chip-erase --confirm-erase [--allow-eeprom-erase]\n"
					"  --read-fuses | --read-lock\n"
					"  --write-fuse NAME HEXBYTE --confirm-fuse\n"
					"  --lock --confirm-lock     locks memory; recovery requires chip erase\n"
					"Fuses: WDTCFG BODCFG OSCCFG SYSCFG0 SYSCFG1 APPEND BOOTEND\n"
					"UPDI pin disabling and locked-device erase are not supported.\n"
					"Outputs must be new files. Writes are read-back verified; no automatic erase.\n", argv[0]);
				return 0;
			}
			if (arg == "-v") { verbose = true; continue; }
			if (arg == "--confirm-erase") { confirmErase = true; continue; }
			if (arg == "--allow-eeprom-erase") { allowEeprom = true; continue; }
			if (arg == "--confirm-fuse") { confirmFuse = true; continue; }
			if (arg == "--confirm-lock") { confirmLock = true; continue; }
			if (!arg.empty() && arg[0] != '-' && !deviceSet) { device = arg; deviceSet = true; continue; }
			if (selected) throw std::runtime_error("only one command is allowed");
			selected = true; action = arg;
			if (arg == "--read-page" || arg == "--write-page") {
				page = tiny202::number(next(), 31); path = next();
			} else if (arg == "--read-hex" || arg == "--write-hex") path = next();
			else if (arg == "--write-fuse") { fuse = next(); value = tiny202::number(next(), 255, 16); }
			else if (arg != "--chip-erase" && arg != "--read-fuses" && arg != "--read-lock" && arg != "--lock")
				throw std::runtime_error("Invalid argument: " + arg);
		}
		if (confirmErase != (action == "--chip-erase") || (allowEeprom && action != "--chip-erase") ||
			confirmFuse != (action == "--write-fuse") || confirmLock != (action == "--lock"))
			throw std::runtime_error("missing or unrelated confirmation option (use --help)");
		tiny202::Image image;
		if (action == "--write-hex") image = tiny202::readHex(path);
		if (action == "--write-page") {
			std::ifstream in(path, std::ios::binary);
			tiny202::Page data;
			if (!in.read(reinterpret_cast<char *>(data.data()), data.size()) || in.peek() != EOF || in.bad())
				throw std::runtime_error("page input must be exactly 64 bytes");
			image[page] = data;
		}
		if (action == "--write-fuse") tiny202::validateFuse(tiny202::findFuse(fuse), value);
		SerialPort port(device.c_str(), verbose);
		Updi updi(port);
		std::array<uint8_t, 3> signature {};
		std::string output;
		try {
			updi.initialize();
			updi.enterProgrammingMode();
			signature = updi.readSignature();
			if (signature == std::array<uint8_t, 3>{0x1e, 0x91, 0x23}) {
				tiny202::Nvm<Updi> nvm(updi);
				if (action == "--write-page" || action == "--write-hex") nvm.write(image);
				else if (action == "--read-page") {
					auto data = nvm.readPage(page);
					output.assign(reinterpret_cast<const char *>(data.data()), data.size());
				} else if (action == "--read-hex") {
					for (unsigned p = 0; p < tiny202::PageCount; ++p) image[p] = nvm.readPage(p);
					output = tiny202::encodeHex(image);
				} else if (action == "--chip-erase") nvm.erase(allowEeprom);
				else if (action == "--write-fuse") nvm.writeFuse(tiny202::findFuse(fuse), value);
				else if (action == "--lock") { nvm.lock(); updi.verifyLocked(); }
				else if (action == "--read-fuses") {
					for (const auto &f : tiny202::Fuses) std::printf("%s = %02X\n", f.name, nvm.readFuse(f.offset));
				} else if (action == "--read-lock") std::printf("LOCKBIT = %02X\n", nvm.readFuse(10));
			}
		} catch (...) {
			try { updi.leaveProgrammingMode(); }
			catch (const std::exception &e) { std::fprintf(stderr, "Cleanup failed: %s\n", e.what()); }
			throw;
		}
		updi.leaveProgrammingMode();
		std::printf("Device signature = 0x%02X%02X%02X\n", signature[0], signature[1], signature[2]);
		if (signature != std::array<uint8_t, 3>{0x1e, 0x91, 0x23}) {
			std::fprintf(stderr, "Signature mismatch: expected 0x1E9123 (ATtiny202)\n"); return 2;
		}
		if (!output.empty()) saveOutput(path, output);
		std::puts("ATtiny202 detected");
		if (action != "signature") std::puts("Operation completed");
		return 0;
	} catch (const std::exception &error) {
		std::fprintf(stderr, "Error: %s\n", error.what()); return 1;
	}
}
