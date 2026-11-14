"""AVR64DU32 NVM P:4 transcripts, 24-bit addresses and device-specific guards."""
import pathlib
import subprocess
import tempfile
import unittest
import test_serialupdi as serial
from test_serialupdi import packet, LINK, KEY, CLEANUP, APP
from test_nvm import record

SIB = b'AVR     P:4D:1-3M2 (A2.KV00S.0)'.ljust(32, b'\x00')
INIT = [packet('00')] + LINK + [packet('55 8b', b'\x00'), packet('55 e6', SIB)] + LINK


def read(address, value):
    return packet('55 08 ' + address.to_bytes(3, 'little').hex(' '), bytes([value]))


def store(address, data):
    opcode = '49' if len(data) == 2 else '48'
    return [packet('55 ' + opcode + ' ' + address.to_bytes(3, 'little').hex(' '), b'\x40'),
            (bytes(data), b'\x40')]


READY = [read(0x1006, 0)]
START = (INIT + KEY + [packet('55 8b', b'\x08'), packet('55 8b', b'\x08')] +
         [read(0x1080 + i, value) for i, value in enumerate([0x1e, 0x96, 0x21])])


def block(address, data):
    result = []
    for offset in range(0, len(data), 256):
        chunk = data[offset:offset+256]
        result.append(packet('55 6a ' + (address + offset).to_bytes(3, 'little').hex(' '), b'\x40'))
        if len(chunk) > 1:
            result.append(packet(f'55 a0 {len(chunk)-1:02x}'))
        result.append(packet('55 24', chunk))
    return result


def page_read(page, data):
    return READY + block(0x800000 + page * 512, data)


def program(page, data):
    result = READY + store(0x1000, [2])
    for offset in range(0, 512, 2):
        result += store(0x800000 + page * 512 + offset, data[offset:offset+2]) + READY
    return result + READY + store(0x1000, [0])


def lock_read(value=0x5cc5c55c):
    return READY + [read(0x1040 + i, b) for i, b in enumerate(value.to_bytes(4, 'little'))]


def erase(cfg=0xd3, verify=False, corrupt_boot=False):
    eeprom = bytes(range(256))
    bootrow = bytes(reversed(range(256)))
    result = (READY + [read(0x1055, cfg)] + block(0x1400, eeprom) + block(0x1100, bootrow) +
              READY + store(0x1000, [0x20]) + READY + store(0x1000, [0]))
    if verify:
        for p in range(128):
            result += page_read(p, bytes([255]) * 512)
    result += block(0x1400, eeprom if cfg & 1 else bytes([255]) * 256)
    after_boot = bootrow if cfg & 2 else bytes([255]) * 256
    if corrupt_boot:
        after_boot = after_boot[:-1] + bytes([after_boot[-1] ^ 1])
    result += block(0x1100, after_boot)
    if not corrupt_boot:
        result += lock_read()
    return result


class Avr64du32Test(unittest.TestCase):
    run_device = serial.SerialUpdiTest.run_device

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.data = bytes(range(256)) * 2
        self.blank = bytes([255]) * 512
        self.input = self.root / 'page.bin'
        self.input.write_bytes(self.data)

    def run_du(self, script=(), args=(), code=0, message=b'Operation completed', **kw):
        self.run_device(START + list(script) + CLEANUP, code, message,
                        args=['-t', 'AVR64DU32', *args], **kw)

    def test_signature_and_canonical_name(self):
        for option in ['-t', '--target']:
            with self.subTest(option=option):
                self.run_device(START + CLEANUP, 0, b'AVR64DU32 detected',
                                args=[option, 'avr64du32'], fragment=True)

    def test_wrong_sib_stops_before_memory_access(self):
        sib = SIB[:10] + b'2' + SIB[11:]
        self.run_device(INIT[:5] + [packet('55 e6', sib)] + CLEANUP, 1,
                        b'unsupported SIB', args=['-t', 'AVR64DU32'])

    def test_wrong_signature_stops_before_erase(self):
        wrong = START[:-1] + [read(0x1082, 0x22)]
        self.run_device(wrong + CLEANUP, 2, b'Signature mismatch',
                        args=['-t', 'AVR64DU32', '--chip-erase', '--allow-eeprom-erase', '--allow-bootrow-erase'])

    def test_last_page_24_bit_read_and_two_repeat_chunks(self):
        out = self.root / 'page.bin.out'
        self.run_du(page_read(127, self.data), ['--read-page', '127', str(out)], fragment=True)
        self.assertEqual(out.read_bytes(), self.data)

    def test_last_page_word_write_and_verify(self):
        self.run_du(page_read(127, self.blank) + program(127, self.data) + page_read(127, self.data),
                    ['--write-page', '127', str(self.input)])

    def test_identical_page_is_not_written(self):
        self.run_du(page_read(0, self.data), ['--write-page', '0', str(self.input)])

    def test_nonblank_page_stops_before_command(self):
        self.run_du(page_read(0, bytes(512)), ['--write-page', '0', str(self.input)],
                    1, b'neither blank nor identical')

    def test_nvm_error_uses_v4_status_mask(self):
        self.run_du([read(0x1006, 0x20)], ['--read-lock'], 1, b'NVM write error')

    def test_flash_failure_clears_command_and_never_continues(self):
        script = (page_read(0, self.blank) + READY + store(0x1000, [2]) +
                  store(0x800000, self.data[:2]) + [read(0x1006, 0x10)] + store(0x1000, [0]))
        self.run_du(script, ['--write-page', '0', str(self.input)], 1, b'NVM write error')

    def test_write_verify_failure(self):
        self.run_du(page_read(0, self.blank) + program(0, self.data) + page_read(0, self.blank),
                    ['--write-page', '0', str(self.input)], 1, b'verify mismatch')

    def test_erase_requires_bootrow_permission(self):
        self.run_du(READY + [read(0x1055, 0xd1)], ['--chip-erase'], 1, b'BROWSAVE=0')

    def test_erase_requires_eeprom_permission(self):
        self.run_du(READY + [read(0x1055, 0xd2)], ['--chip-erase'], 1, b'EESAVE=0')

    def test_erase_preserves_eeprom_bootrow_and_verifies_lock32(self):
        self.run_du(erase(), ['--chip-erase'])

    def test_erase_all_regions_with_explicit_permissions(self):
        self.run_du(erase(0xd0), ['--chip-erase', '--allow-eeprom-erase', '--allow-bootrow-erase'])

    def test_erase_verifies_entire_flash_including_last_page(self):
        self.run_du(erase(verify=True), ['--chip-erase', '--verify-erase-flash'])

    def test_bootrow_verify_failure_stops_hex_write(self):
        path = self.root / 'firmware.hex'
        path.write_text(record(0, [1]) + record(0, [], 1))
        self.run_du(erase(corrupt_boot=True), ['--write-hex', str(path)],
                    1, b'Boot Row verification failed')

    def test_hex_auto_erase_and_upper_flash_byte(self):
        path = self.root / 'firmware.hex'
        path.write_text(record(0xffff, [0x12]) + record(0, [], 1))
        data = self.blank[:-1] + b'\x12'
        self.run_du(erase() + page_read(127, self.blank) + program(127, data) + page_read(127, data),
                    ['--write-hex', str(path)])

    def test_hex_upper_flash_without_erase(self):
        path = self.root / 'firmware.hex'
        path.write_text(record(0xffff, [0x12]) + record(0, [], 1))
        self.run_du(page_read(127, self.blank[:-1] + b'\x12'),
                    ['--write-hex', str(path), '--no-chip-erase'])

    def test_full_hex_export(self):
        out = self.root / 'backup.hex'
        script = []
        for page in range(128):
            script += page_read(page, self.data)
        self.run_du(script, ['--read-hex', str(out)])
        text = out.read_text()
        self.assertIn(record(0xfff0, self.data[-16:]), text)
        self.assertTrue(text.endswith(':00000001FF\n'))
        self.assertEqual(len(text.splitlines()), 4097)

    def test_read_lock_32_bit(self):
        self.run_du(lock_read(), ['--read-lock'], message=b'LOCKBIT = 5CC5C55C')

    def test_fuse_write_uses_eeprom_command_and_clears_it(self):
        script = (READY + [read(0x1056, 8)] + READY + store(0x1000, [0x13]) +
                  store(0x1056, [9]) + READY + store(0x1000, [0]) + READY + [read(0x1056, 9)])
        self.run_du(script, ['--write-fuse', 'SYSCFG1', '09', '--confirm-fuse'])

    def test_read_fuses_including_pdi_configuration(self):
        script = []
        for address in [0x1050, 0x1051, 0x1052, 0x1055, 0x1056, 0x1057, 0x1058, 0x105a, 0x105b]:
            script += READY + [read(address, 0)]
        self.run_du(script, ['--read-fuses'], message=b'PDICFGH = 00')

    def test_lock32_then_verify_after_reset(self):
        script = lock_read() + READY + [read(0x105a, 3)] + READY + [read(0x105b, 0)]
        script += READY + store(0x1000, [0x13])
        for i, value in enumerate(bytes.fromhex('a3 3a 3a a3')):
            script += store(0x1040 + i, [value]) + READY
        script += READY + store(0x1000, [0]) + lock_read(0xa33a3aa3)
        script += [packet('55 c8 59'), packet('55 c8 00'), packet('55 8b', b'\x01')]
        self.run_du(script, ['--lock', '--confirm-lock'])

    def test_lock_refuses_pdid_configuration(self):
        self.run_du(lock_read() + READY + [read(0x105a, 0x52)], ['--lock', '--confirm-lock'],
                    1, b'refusing lock with non-default PDICFG')

    def test_invalid_device_page_fuse_and_options_before_open(self):
        for args in [['-t', 'AVR64DU32T'], ['-t', 'AVR64DU32', '-t', 'ATtiny202'],
                     ['-t', 'AVR64DU32', '--read-page', '128', 'out'],
                     ['-t', 'AVR64DU32', '--read-page', '', 'out'],
                     ['-t', 'AVR64DU32', '--write-fuse', 'APPEND', '00', '--confirm-fuse'],
                     ['-t', 'AVR64DU32', '--write-fuse', 'PDICFGL', '03', '--confirm-fuse'],
                     ['-t', 'AVR64DU32', '--write-fuse', 'SYSCFG0', 'C0', '--confirm-fuse'],
                     ['-t', 'AVR64DU32', '--write-fuse', 'SYSCFG1', 'F8', '--confirm-fuse'],
                     ['-t', 'AVR64DU32', '--write-fuse', 'OSCCFG', '02', '--confirm-fuse'],
                     ['-t', 'AVR64DU32', '--write-fuse', 'BOOTSIZE', '81', '--confirm-fuse'],
                     ['--chip-erase', '--allow-bootrow-erase'],
                     ['-t', 'AVR64DU32', '--read-lock', '--allow-bootrow-erase']]:
            with self.subTest(args=args):
                result = subprocess.run([str(APP), '-p', '/no-such-port', *args], capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn(b'open serial port', result.stderr)

    def test_page_size_validated_after_device_option(self):
        for size in [64, 511, 513]:
            self.input.write_bytes(bytes(size))
            result = subprocess.run([str(APP), '-p', '/no-such-port', '--write-page', '127', str(self.input),
                                     '--target', 'avr64du32'], capture_output=True)
            self.assertIn(b'exactly 512 bytes', result.stderr)
            self.assertNotIn(b'open serial port', result.stderr)

    def test_hex_above_64k_rejected_before_open(self):
        path = self.root / 'bad.hex'
        path.write_text(record(0, [0, 1], 4) + record(0, [1]) + record(0, [], 1))
        result = subprocess.run([str(APP), '-p', '/no-such-port', '-t', 'AVR64DU32', '--write-hex', str(path)],
                                capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'invalid HEX', result.stderr)
        self.assertNotIn(b'open serial port', result.stderr)
