// Exercise the actual UPDI/serial implementation without exposing a raw-memory CLI.
#define main avrupdi_application_main
#include "../main.cpp"
#undef main

int main(int argc, char **argv)
{
	if (argc != 4 && argc != 5) return 2;
	try {
		SerialPort port(argv[1], false);
		const auto &device = argc == 5 ? avrprog::findDevice(argv[4]) : avrprog::ATtiny202;
		Updi updi(port, device);
		const auto address = avrprog::number(argv[2], 0xffffff);
		const auto size = avrprog::number(argv[3], 0x1000001);
		const auto data = updi.readBlock(address, size);
		for (auto byte : data) std::printf("%02X", byte);
		std::puts(" OK");
		return 0;
	} catch (const std::exception &error) {
		std::fprintf(stderr, "%s\n", error.what());
		return 1;
	}
}
