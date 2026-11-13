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
		if (readCs(0x0B) & 0x08) return;
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
	void leaveProgrammingMode()
	{
		if (!active_) return;
		reset();
		writeCs(0x03, 0x0C);
		active_ = false;
	}

private:
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

int main(int argc, char **argv)
{
	const char *device = "/dev/ttyUSB0";
	bool verbose = false;
	bool deviceSet = false;
	for (int i = 1; i < argc; ++i) {
		const std::string arg = argv[i];
		if (arg == "-h" || arg == "--help") {
			std::printf("Usage: %s [-v] [/dev/ttyUSB0]\nRead ATtiny202 signature using SerialUPDI (115200 baud, 8E2).\n", argv[0]);
			return 0;
		}
		if (arg == "-v")
			verbose = true;
		else if (!arg.empty() && arg[0] != '-' && !deviceSet) {
			device = argv[i];
			deviceSet = true;
		} else {
			std::fprintf(stderr, "Invalid argument: %s (use --help)\n", argv[i]);
			return 1;
		}
	}
	try {
		SerialPort port(device, verbose);
		Updi updi(port);
		std::array<uint8_t, 3> signature { };
		try {
			updi.initialize();
			updi.enterProgrammingMode();
			signature = updi.readSignature();
		} catch (...) {
			try {
				updi.leaveProgrammingMode();
			} catch (const std::exception &error) {
				std::fprintf(stderr, "Cleanup failed: %s\n", error.what());
			}
			throw;
		}
		updi.leaveProgrammingMode();
		std::printf("Device signature = 0x%02X%02X%02X\n", signature[0], signature[1], signature[2]);
		if (signature != std::array<uint8_t, 3> { 0x1E, 0x91, 0x23 }) {
			std::fprintf(stderr, "Signature mismatch: expected 0x1E9123 (ATtiny202)\n");
			return 2;
		}
		std::puts("ATtiny202 detected");
		return 0;
	} catch (const std::exception &error) {
		std::fprintf(stderr, "Error: %s\n", error.what());
		return 1;
	}
}
