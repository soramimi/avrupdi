// Exercise the actual UPDI/serial implementation without exposing a raw-memory CLI.
#define main avrupdi_application_main
#include "../main.cpp"
#undef main

int main(int argc, char **argv)
{
	if (argc != 4) return 2;
	try {
		SerialPort port(argv[1], false);
		Updi updi(port);
		const auto address = tiny202::number(argv[2], 65535);
		const auto size = tiny202::number(argv[3], 65537);
		const auto data = updi.readBlock(address, size);
		for (auto byte : data) std::printf("%02X", byte);
		std::puts(" OK");
		return 0;
	} catch (const std::exception &error) {
		std::fprintf(stderr, "%s\n", error.what());
		return 1;
	}
}
