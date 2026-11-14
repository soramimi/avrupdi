# ATtiny202 SerialUPDI プログラマー

SerialUPDI 経由で ATtiny202 のシグネチャ、Flash、Fuse、ロックを扱う Linux 向けの C++ プログラムです。
`avrdude -c serialupdi -P /dev/ttyUSB0 -p attiny202 -v` のシグネチャ確認に相当する処理を、シリアルポートを直接操作して行います。実行時に avrdude は不要です。

ATtiny202 の実機で、`0x1E9123` の読み出しを確認済みです。シグネチャは型番を識別する値で、個体固有のシリアル番号ではありません。

## 必要な環境

- Linux
- C++17 対応の `g++` と GNU Make
- テストを実行する場合は Python 3（標準ライブラリのみ使用）
- SerialUPDI 回路を備えた USB–UART アダプターと、電源を供給した ATtiny202
- 使用するシリアルポートへの読み書き権限

avrdude の `serialupdi` で通信できている接続を利用できます。通常の UART の TX/RX 接続ではなく、UPDI の単線通信と送信エコーに対応した回路が必要です。ターゲットとアダプターの GND を共通にし、信号電圧を合わせてください。

## ビルドと実行

```bash
make
./myapp
```

デフォルトのポートは `/dev/ttyUSB0` です。ポート指定と通信ログの表示は次のように行います。

```bash
./myapp /dev/ttyUSB1
./myapp -v /dev/ttyUSB0
./myapp --help
```

成功時の出力：

```text
Device signature = 0x1E9123
ATtiny202 detected
```

`-v` を指定すると、送信データ `TX`、送信エコー `ECHO`、ターゲットからの応答 `RX` を16進数で標準エラー出力に表示します。結果は標準出力、エラーは標準エラー出力に表示します。

終了コード：

| 値 | 意味 |
| --- | --- |
| `0` | 指定操作に成功（省略時は識別、ヘルプも `0`） |
| `1` | 引数、ポート操作、通信、SIB、モード移行、終了処理などのエラー |
| `2` | 読み出したシグネチャが ATtiny202 の期待値と不一致 |

## 通信処理

1. シリアルポートを 115200 bps・8E2（8ビット、偶数パリティ、2ストップビット）、フロー制御なしで開きます。
2. UPDI リンクを初期化します。初回接続に失敗した場合は、300 bps・8E1 で double BREAK を送信して再試行します。
3. 32バイトの SIB を取得し、`tinyAVR` ファミリーと NVM インターフェースのバージョン `0` を確認します。`P:0` のようなフィールドのコロン後を判定します。
4. NVM プログラミングキーを送信し、プログラミングモードへの移行を確認します。
5. `0x1100`、`0x1101`、`0x1102` から1バイトずつ読み出します。
6. リセット要求・解除と UPDI 無効化を行い、ポート設定の復元を試みて閉じます。

部分的な読み書き、送信エコーの照合、タイムアウトに対応しています。送信とエコー受信の共通期限、および応答受信の期限はそれぞれ1秒です。モード移行のポーリング期限は500ミリ秒ですが、個々の通信待ちにより全体ではそれを超える場合があります。

対象は ATtiny202 / NVM interface version 0 のみです。すべての追加操作で SIB とシグネチャを確認し、不一致の場合は書き込みません。EEPROM の独立した読み書きコマンドは対象外です。ロック状態ではエラーになり、自動消去しません。読み出し操作でもターゲットはリセットされます。

## Flash ページと Intel HEX

従来の引数なし実行、位置引数のポート指定、`-v`、ヘルプ、シグネチャ出力と終了コードは維持します。追加コマンドは1回の起動につき1つだけ指定できます。ファイル入力と引数の検証はポートを開く前に行います。読み出し先は**新規ファイル**に限定し、既存のバックアップやシンボリックリンクを上書きしません。

```bash
./myapp /dev/ttyUSB0 --read-page 0 page0.bin
./myapp /dev/ttyUSB0 --write-page 0 page0.bin
./myapp /dev/ttyUSB0 --read-hex backup.hex
./myapp /dev/ttyUSB0 --write-hex firmware.hex
```

ページ番号は十進数または `0x` 付き16進数の 0～31、バイナリは厳密に64バイトです。Flash は2048バイト。HEX アドレスは Flash 先頭基準の **バイト単位** `0x0000`～`0x07FF` で、UPDI のシステムバスでは `0x8000` を加えます。HVPP のワードアドレスは使いません。Flash バッファはリトルエンディアンの16ビット STS でロードし、アドレス・データそれぞれの ACK を検証します。

HEX はデータ（00）、EOF（01）、拡張セグメント（02）、拡張リニアアドレス（04）に対応します。長さ、チェックサム、範囲、EOF を検査し、重複アドレス（同値を含む）、EOF 後のレコード、開始アドレス（03/05）などの未対応レコードを拒否します。空行・CRLF は許容しますが、データのない入力は拒否します。出力は全領域を16バイトレコードで保存し、空の Flash も往復可能です。

**書き込みは消去を伴いません。** データのある全選択ページを最初に読み、ページ全体が `FF` または入力と完全一致の場合だけ許可します。HEX の選択ページ内の未指定バイトは `FF` 埋めです。未選択ページは変更しません。完全一致ページは再書き込みせず、変更するページはバッファクリア（04）→ワード転送→ページ書き込み（01）→全64バイトの読み戻し比較を行います。後方のページが事前確認で不適合なら、前方のページも書き込みません。

## チップ消去

```bash
./myapp /dev/ttyUSB0 --chip-erase --confirm-erase
# EESAVE=0 のデバイスで EEPROM も消してよい場合だけ指定
./myapp /dev/ttyUSB0 --chip-erase --confirm-erase --allow-eeprom-erase
```

**Flash 全領域を消去します。** NVMCTRL の CHIP ERASE（05）を使用し、全 Flash の `FF`、EEPROM の保持／消去、ロック値 `C5` を検証します。EESAVE=0 なら EEPROM も消えるため、追加指定がなければコマンド発行前に拒否します。EESAVE=1 なら EEPROM の消去前内容と消去後内容を比較します。保護を外す目的で Fuse を自動変更しません。

ロック済みデバイスではシグネチャや EESAVE を読めず、ATtiny202 と確認できません。そのため今回の実装は**ロック済みデバイスの消去／解除には非対応**です。CHIPERASE キーを送信する機能もありません。ロック後の復旧には、対象を確認して明示消去できる別のプログラマーが必要です。

## Fuse とロック

```bash
./myapp --read-fuses
./myapp --read-lock
./myapp --write-fuse SYSCFG1 FF --confirm-fuse
# バックアップと Flash の検証を済ませ、保護が必要な場合だけ実行
./myapp --lock --confirm-lock
```

Fuse は `WDTCFG`、`BODCFG`、`OSCCFG`、`SYSCFG0`、`SYSCFG1`、`APPEND`、`BOOTEND` の名前で指定します。値は16進数（例 `FF` または `0xFF`）です。旧 AVR の low/high/extended は受け付けません。予約オフセットはアクセス対象にせず、予約ビットは1を要求し、予約フィールド値・Flash を超える区画設定を拒否します。`SYSCFG0.RSTPINCFG` は UPDI（01）だけ許可し、GPIO／RESET への変更は明示指定があっても拒否します。

**Fuse はリセット後のクロック、BOD、WDT、CRC、起動時間、Flash 区画などを変更します。** 供給電圧とファームウェアに合う値をデータシートで確認してください。変更は1バイトずつ WFU（07）で行い、読み戻し比較後に終了処理でリセットします。既存値と同一なら書き込みません。

ロックは任意の数値を受け付けず `3A` の設定に限定します。`C5` が解除状態であることを確認後、書き込み・読み戻し・リセット後の LOCKSTATUS を検証します。ロック後はメモリを読み出せません。ロック解除のため `C5` を書き込むコマンドや自動消去はありません。検証に失敗してもロックは既に有効になっている可能性があります。

## 失敗時と実機未検証事項

NVM STATUS の FBUSY/EEBUSY を期限付き（2秒）で監視し、WRERROR、ACK 不一致、エコー不一致、読み戻し不一致、タイムアウトで停止します。個々の通信にも既存の1秒期限があり、全体の待ち時間がポーリング期限を超える場合があります。リンク確立後は失敗時も終了処理を試み、元のエラーを残します。消去・書き込みコマンドを自動再試行しません。

複数ページの書き込みはトランザクションではありません。途中の通信断／電源断では、前のページだけが書かれたり、消去・Fuse・ロックの結果が不確定になったりします。自動ロールバックはしません。終了処理のリセットにより、不完全なプログラムが動き始める可能性もあります。失敗後は装置を安全な状態にしてから、読み出しと比較で状態を確認してください。

以前のシグネチャ取得以外の追加機能は**実機未検証**です。PTY は通信列とソフトウェアの判断を検証しますが、実際の NVM 書き込み完了、電圧・波形、電源断耐性、Fuse の再起動時動作、ロックの実際の発効は保証しません。実機ではバックアップ後に、Flash 読み出し→空ページへの書き込み／検証→明示消去と EEPROM 保持→安全な Fuse→最後にロックの順で確認が必要です。

## テスト

```bash
make test
```

Python の疑似端末（PTY）でターゲットの応答を模擬します。実機は不要です。正常な読み出しと受信の分割、シグネチャ不一致、不正・未対応の SIB、キー拒否、エコー不一致、ロック状態、応答タイムアウト、double BREAK による復帰を確認します。

PTY テストでは実際の電気的特性や UART のパリティ・ストップビットの動作は検証できません。通信設定やコマンド列を変更した場合は、実機でも確認してください。

## Makefile の主なターゲット

| ターゲット | 内容 |
| --- | --- |
| `make` | `myapp` をビルド |
| `make run` | ビルド済みの `myapp` をデフォルト設定で実行 |
| `make test` | ビルド後、疑似端末テストを実行 |
| `make dude` | 比較用の avrdude コマンドを実行（avrdude が必要） |
| `make clean` | 実行ファイル、オブジェクト、依存関係ファイルを削除 |
| `make install` | ビルド済みの `myapp` を `~/.local/bin/` に配置 |
| `make uninstall` | `~/.local/bin/myapp` を削除 |

`run` と `install` はビルドを行わないため、先に `make` を実行してください。`install` の配置先ディレクトリは事前に用意する必要があります。

## 問題が起きた場合

- ポートを開けない場合は、デバイスパスとアクセス権、ほかのアプリケーションによる使用状況を確認してください。
- エコー不一致やタイムアウトの場合は、SerialUPDI 回路、配線、電源、GND、信号電圧を確認してください。
- `unsupported SIB` の場合は、エラーに表示された SIB と `-v` の受信ログを確認してください。
- avrdude と比較する場合は、同じ回路とポートで `make dude` を実行してください。同時に両プログラムを起動しないでください。

## ファイル構成

- `main.cpp`：シリアル通信、UPDI 制御、コマンドライン処理
- `nvm.h`：ATtiny202 の NVM v0 操作、事前確認、読み戻し検証
- `image.h`：Flash 形状、Intel HEX、Fuse 名と値の検証
- `main.h`：現時点では未使用のヘッダーファイル
- `Makefile`：ビルド・実行・テスト
- `tests/test_serialupdi.py`：既存のリンク／シグネチャ通信テスト
- `tests/test_nvm.py`：NVM、Flash、HEX、Fuse、ロック、安全な拒否の PTY テスト
- `AGENTS.md`：開発エージェント向けの作業指針

## 参考資料

- [avrdude の SerialUPDI 説明](https://avrdudes.github.io/avrdude/8.0/avrdude_22.html)
- [avrdude の SerialUPDI 制御](https://github.com/avrdudes/avrdude/blob/main/src/serialupdi.c)
- [avrdude の UPDI 通信層](https://github.com/avrdudes/avrdude/blob/main/src/updi_link.c)
- [avrdude のデバイス定義](https://github.com/avrdudes/avrdude/blob/main/src/avrdude.conf.in)

### 追加実装で確認した一次資料

- [Microchip ATtiny202/204/402/404/406 データシート](https://onlinedocs.microchip.com/oxy/GUID-5A56DB3A-31E1-4F46-984F-39186535C84E-en-US-7/index.html)：6章（メモリ、Fuse）、9章（NVMCTRL）、30章（UPDI）。
- [ページバッファロード](https://onlinedocs.microchip.com/oxy/GUID-5A56DB3A-31E1-4F46-984F-39186535C84E-en-US-7/GUID-1E0E5B94-2405-4F7A-B989-3A53D95FCA2E.html) と [Flash プログラミング](https://onlinedocs.microchip.com/oxy/GUID-5A56DB3A-31E1-4F46-984F-39186535C84E-en-US-7/GUID-4A1ECDF1-BD0C-4924-94D0-72A980691112.html)。
- [SYSCFG0 / EESAVE / RSTPINCFG](https://onlinedocs.microchip.com/oxy/GUID-5A56DB3A-31E1-4F46-984F-39186535C84E-en-US-7/GUID-5BE14054-06DE-4EE5-8407-A0DCF966D37B.html)、[Lockbits](https://onlinedocs.microchip.com/oxy/GUID-5A56DB3A-31E1-4F46-984F-39186535C84E-en-US-7/GUID-A3BCA5E8-61A3-45B1-85F8-79848B91C807.html)。
- Microchip の [pymcuprog NVM P:0 実装](https://github.com/microchip-pic-avr-tools/pymcuprog/blob/main/pymcuprog/serialupdi/nvmp0.py) と [ATtiny202 定義](https://github.com/microchip-pic-avr-tools/pymcuprog/blob/main/pymcuprog/deviceinfo/devices/attiny202.py)：アドレス、形状、16ビット Flash ロード、UPDI からの直接 CTRLA 書き込み、Fuse の ADDR/DATA/WFU 手順を照合。
- [avrhvpp のホスト実装](https://github.com/soramimi/avrhvpp/blob/8888cfd53999f4ef4bf54f60fb4ab239b29c0cd6/app/main.cpp) を読み取り専用で参考にし、選択ページ内の FF 埋めと全ページ事前確認を採用。通信プロトコルと CLI は SerialUPDI 向けであり、avrhvpp のドロップイン互換ではありません。
