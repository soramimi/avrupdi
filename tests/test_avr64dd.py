"""AVR64DD28/32: P:2、16バイト SIB、24ビット通信と機種固有の保護。"""
import pathlib
import subprocess
import tempfile
import unittest
import test_serialupdi as serial
from test_serialupdi import packet, LINK, KEY, CLEANUP, APP
from test_avr64du32 import read, store, block
from test_nvm import record

SIB = b'    AVR P:2D:1-3'
INIT = [packet('00')] + LINK + [packet('55 8b', b'\x00'), packet('55 e5', SIB)] + LINK
READY = [read(0x1002, 0)]


def start(signature):
    return (INIT + KEY + [packet('55 8b', b'\x08'), packet('55 8b', b'\x08')] +
            [read(0x1100 + i, value) for i, value in enumerate(signature)])


def page_read(page, data):
    return READY + block(0x800000 + page * 512, data)


def program(page, data):
    result = READY + store(0x1000, [2])
    for offset in range(0, 512, 2):
        result += store(0x800000 + page * 512 + offset, data[offset:offset+2]) + READY
    return result + READY + store(0x1000, [0])


def lock_read(value=0x5cc5c55c):
    return READY + [read(0x1040 + i, b) for i, b in enumerate(value.to_bytes(4, 'little'))]


def erase(cfg=0xd1, verify=False, corrupt_eeprom=False, lock=0x5cc5c55c):
    eeprom = bytes(range(256))
    result = (READY + [read(0x1055, cfg)] + block(0x1400, eeprom) +
              READY + store(0x1000, [0x20]) + READY + store(0x1000, [0]))
    if verify:
        for p in range(128):
            result += page_read(p, bytes([255]) * 512)
    after = eeprom if cfg & 1 else bytes([255]) * 256
    if corrupt_eeprom:
        after = after[:-1] + bytes([after[-1] ^ 1])
    result += block(0x1400, after)
    if not corrupt_eeprom:
        result += lock_read(lock)
    return result


class DdTests:
    run_device = serial.SerialUpdiTest.run_device

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.data = bytes(range(256)) * 2
        self.blank = bytes([255]) * 512
        self.input = self.root / 'page.bin'
        self.input.write_bytes(self.data)

    def run_dd(self, script=(), args=(), code=0, message=b'Operation completed', **kw):
        self.run_device(start(self.signature) + list(script) + CLEANUP, code, message,
                        args=['-t', self.target, *args], **kw)

    def test_signature_short_and_long_target_options(self):
        for option in ['-t', '--target']:
            with self.subTest(option=option):
                self.run_device(start(self.signature) + CLEANUP, 0, (self.target + ' detected').encode(),
                                args=[option, self.target.lower()], fragment=True)

    def test_family_padding_and_wrong_sib(self):
        for sib, valid in [(b'AVR     P:2D:1-3', True), (b'    AVR P:4D:1-3', False),
                           (b'tinyAVR P:2D:1-3', False), (b'    AVR P-2D:1-3', False)]:
            with self.subTest(sib=sib):
                init = INIT[:5] + [packet('55 e5', sib)]
                script = init + (start(self.signature)[6:] if valid else []) + CLEANUP
                self.run_device(script, 0 if valid else 1,
                                b'detected' if valid else b'unsupported SIB', args=['-t', self.target])

    def test_other_dd_signature_blocks_erase(self):
        wrong = self.signature[:2] + bytes([self.signature[2] ^ 1])
        self.run_device(start(wrong) + CLEANUP, 2, b'Signature mismatch',
                        args=['-t', self.target, '--chip-erase', '--allow-eeprom-erase'])

    def test_last_page_read(self):
        out = self.root / 'last.bin'
        self.run_dd(page_read(127, self.data), ['--read-page', '127', str(out)], fragment=True)
        self.assertEqual(out.read_bytes(), self.data)

    def test_last_page_write_and_verify(self):
        self.run_dd(page_read(127, self.blank) + program(127, self.data) + page_read(127, self.data),
                    ['--write-page', '127', str(self.input)])

    def test_identical_page_skips_write(self):
        self.run_dd(page_read(0, self.data), ['--write-page', '0', str(self.input)])

    def test_nonblank_page_blocks_write(self):
        self.run_dd(page_read(0, bytes(512)), ['--write-page', '0', str(self.input)],
                    1, b'neither blank nor identical')

    def test_status_error_mask_and_address(self):
        for status in [0x10, 0x20, 0x40]:
            with self.subTest(status=status):
                self.run_dd([read(0x1002, status)], ['--read-lock'], 1, b'NVM write error')

    def test_busy_then_ready(self):
        self.run_dd([read(0x1002, 3)] + lock_read(), ['--read-lock'], message=b'LOCKBIT = 5CC5C55C')

    def test_flash_error_clears_command_and_stops(self):
        script = (page_read(0, self.blank) + READY + store(0x1000, [2]) +
                  store(0x800000, self.data[:2]) + [read(0x1002, 0x20)] + store(0x1000, [0]))
        self.run_dd(script, ['--write-page', '0', str(self.input)], 1, b'NVM write error')

    def test_flash_verify_failure(self):
        self.run_dd(page_read(0, self.blank) + program(0, self.data) + page_read(0, self.blank),
                    ['--write-page', '0', str(self.input)], 1, b'verify mismatch')

    def test_fuse_write_and_readback(self):
        for fuse, offset, old, value in [('SYSCFG1', 6, 8, 0x10), ('OSCCFG', 2, 0, 1),
                                         ('SYSCFG0', 5, 0xd0, 0xd9), ('CODESIZE', 7, 0, 0x80),
                                         ('BOOTSIZE', 8, 0, 1)]:
            with self.subTest(fuse=fuse):
                script = (READY + [read(0x1050 + offset, old)] + READY + store(0x1000, [0x13]) +
                          store(0x1050 + offset, [value]) + READY + store(0x1000, [0]) +
                          READY + [read(0x1050 + offset, value)])
                self.run_dd(script, ['--write-fuse', fuse, f'{value:02X}', '--confirm-fuse'])

    def test_fuse_error_clears_command(self):
        script = (READY + [read(0x1056, 8)] + READY + store(0x1000, [0x13]) +
                  store(0x1056, [0x10]) + [read(0x1002, 0x10)] + store(0x1000, [0]))
        self.run_dd(script, ['--write-fuse', 'SYSCFG1', '10', '--confirm-fuse'], 1, b'NVM write error')

    def test_read_fuses_without_pdicfg(self):
        script = []
        for offset in [0, 1, 2, 5, 6, 7, 8]:
            script += READY + [read(0x1050 + offset, 0)]
        self.run_dd(script, ['--read-fuses'], message=b'BOOTSIZE = 00')

    def test_lock_32_bits_without_pdicfg(self):
        script = lock_read() + READY + store(0x1000, [0x13])
        for i, value in enumerate(bytes.fromhex('a3 3a 3a a3')):
            script += store(0x1040 + i, [value]) + READY
        script += READY + store(0x1000, [0]) + lock_read(0xa33a3aa3)
        script += [packet('55 c8 59'), packet('55 c8 00'), packet('55 8b', b'\x01')]
        self.run_dd(script, ['--lock', '--confirm-lock'])

    def test_erase_preserves_eeprom_without_bootrow_access(self):
        self.run_dd(erase(), ['--chip-erase'])

    def test_erase_requires_eeprom_permission(self):
        self.run_dd(READY + [read(0x1055, 0xd0)], ['--chip-erase'], 1, b'EESAVE=0')

    def test_erase_eeprom_and_verify_entire_flash(self):
        self.run_dd(erase(0xd0, verify=True),
                    ['--chip-erase', '--allow-eeprom-erase', '--verify-erase-flash'])

    def test_erase_error_clears_command(self):
        script = (READY + [read(0x1055, 0xd1)] + block(0x1400, bytes(256)) +
                  READY + store(0x1000, [0x20]) + [read(0x1002, 0x20)] + store(0x1000, [0]))
        self.run_dd(script, ['--chip-erase'], 1, b'NVM write error')

    def test_erase_eeprom_verify_failure_stops_hex_write(self):
        path = self.root / 'firmware.hex'
        path.write_text(record(0, [1]) + record(0, [], 1))
        self.run_dd(erase(corrupt_eeprom=True), ['--write-hex', str(path)],
                    1, b'EEPROM verification failed')

    def test_erase_verifies_32_bit_unlock_value(self):
        self.run_dd(erase(lock=0x00c5c55c), ['--chip-erase'], 1, b'lock verification failed')

    def test_hex_auto_erase_and_upper_flash_byte(self):
        path = self.root / 'firmware.hex'
        path.write_text(record(0xffff, [0x12]) + record(0, [], 1))
        data = self.blank[:-1] + b'\x12'
        self.run_dd(erase() + page_read(127, self.blank) + program(127, data) + page_read(127, data),
                    ['--write-hex', str(path)])

    def test_hex_without_erase(self):
        path = self.root / 'firmware.hex'
        path.write_text(record(0xffff, [0x12]) + record(0, [], 1))
        data = self.blank[:-1] + b'\x12'
        self.run_dd(page_read(127, self.blank) + program(127, data) + page_read(127, data),
                    ['--write-hex', str(path), '--no-chip-erase'])

    def test_full_hex_export(self):
        out = self.root / 'backup.hex'
        script = []
        for page in range(128):
            script += page_read(page, self.data)
        self.run_dd(script, ['--read-hex', str(out)])
        text = out.read_text()
        self.assertIn(record(0xfff0, self.data[-16:]), text)
        self.assertTrue(text.endswith(':00000001FF\n'))
        self.assertEqual(len(text.splitlines()), 4097)

    def test_invalid_cli_before_open(self):
        cases = [['--read-page', '128', 'out'], ['--chip-erase', '--allow-bootrow-erase']]
        for fuse, value in [('PDICFGL', '03'), ('APPEND', '00'), ('BOOTEND', '00'),
                            ('SYSCFG0', 'C0'), ('SYSCFG0', 'D2'), ('SYSCFG0', 'D4'),
                            ('OSCCFG', '02'), ('OSCCFG', '08'), ('OSCCFG', '10'),
                            ('SYSCFG1', '00'), ('SYSCFG1', '18'), ('SYSCFG1', '28'),
                            ('BODCFG', '80'), ('BODCFG', '03'), ('WDTCFG', '0C'),
                            ('CODESIZE', '81'), ('BOOTSIZE', '81')]:
            cases.append(['--write-fuse', fuse, value, '--confirm-fuse'])
        for args in cases:
            with self.subTest(args=args):
                result = subprocess.run([str(APP), '-p', '/no-such-port', '-t', self.target, *args],
                                        capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn(b'open serial port', result.stderr)

    def test_page_length_and_hex_range_before_open(self):
        for size in [64, 511, 513]:
            with self.subTest(size=size):
                self.input.write_bytes(bytes(size))
                result = subprocess.run([str(APP), '-p', '/no-such-port', '--write-page', '127',
                                         str(self.input), '--target', self.target], capture_output=True)
                self.assertIn(b'exactly 512 bytes', result.stderr)
                self.assertNotIn(b'open serial port', result.stderr)
        path = self.root / 'bad.hex'
        path.write_text(record(0, [0, 1], 4) + record(0, [1]) + record(0, [], 1))
        result = subprocess.run([str(APP), '-p', '/no-such-port', '-t', self.target,
                                 '--write-hex', str(path)], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'invalid HEX', result.stderr)
        self.assertNotIn(b'open serial port', result.stderr)


class Avr64dd28Test(DdTests, unittest.TestCase):
    target = 'AVR64DD28'
    signature = bytes.fromhex('1e 96 1b')


class Avr64dd32Test(DdTests, unittest.TestCase):
    target = 'AVR64DD32'
    signature = bytes.fromhex('1e 96 1a')
