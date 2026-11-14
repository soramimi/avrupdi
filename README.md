# ATtiny202 / AVR64DU32 SerialUPDI プログラマー

SerialUPDI 経由で ATtiny202 と AVR64DU32 のシグネチャ、Flash、Fuse、ロックを扱う Linux 向けの C++ プログラムです。
`avrdude -c serialupdi -P /dev/ttyUSB0 -p attiny202 -v` のシグネチャ確認に相当する処理を、シリアルポートを直接操作して行います。実行時に avrdude は不要です。

ATtiny202 の実機で、`0x1E9123` の読み出しを確認済みです。シグネチャは型番を識別する値で、個体固有のシリアル番号ではありません。

## 対応デバイス

| 識別名（`-p`） | シグネチャ | NVM / アドレス幅 | Flash / ページ | シグネチャのアドレス |
| --- | --- | --- | --- | --- |
| `ATtiny202`（既定） | `1E 91 23` | P:0 / 16ビット | 2048 / 64バイト（32ページ） | `0x1100` |
| `AVR64DU32` | `1E 96 21` | P:4 / 24ビット | 65536 / 512バイト（128ページ） | `0x1080` |

識別名・表示名・定義シンボルに梱包記号の `T` は付けません。`-p` / `--part` の値は大文字・小文字を区別せず、`AVR64DU32T` は受け付けません。自動判別ではなく明示選択です。指定を省略した場合は従来どおり ATtiny202 を対象にします。SIB のファミリーと NVM バージョン、さらにシグネチャを照合し、不一致のデバイスに書き込みや消去を行いません。

AVR64DU32 の実行例：

```bash
make
./myapp -p AVR64DU32 /dev/ttyUSB0
./myapp --part AVR64DU32 -v /dev/ttyUSB0
./myapp -p AVR64DU32 --read-page 127 last-page.bin
./myapp -p AVR64DU32 --read-hex backup-du.hex
./myapp -p AVR64DU32 --read-fuses
./myapp -p AVR64DU32 --read-lock
```

識別成功時は次のように表示します。

```text
Device signature = 0x1E9621
AVR64DU32 detected
```

AVR64DU32 のページバイナリは厳密に512バイト、ページ番号は0～127です。HEX のアドレスは Flash 先頭基準のバイト単位 `0x0000`～`0xFFFF` です。UPDI 上では `0x800000` を加えた24ビットアドレスを使用します。ページごとに256バイトの連続読み出しを2回行います。

### AVR64DU32 の書き込み・消去

```bash
# 消去せず、空または完全一致のページに書き込む
./myapp -p AVR64DU32 --write-page 0 page-du.bin
./myapp -p AVR64DU32 --write-hex firmware-du.hex --no-chip-erase
# EEPROM と Boot Row の消去を許可して全消去し、HEX を書き込む
./myapp -p AVR64DU32 --write-hex firmware-du.hex --allow-eeprom-erase --allow-bootrow-erase
# 全消去後に Flash 全領域も検証する
./myapp -p AVR64DU32 --chip-erase --allow-eeprom-erase --allow-bootrow-erase --verify-erase-flash
```

`--write-hex` は両デバイスとも既定で全消去します。AVR64DU32 では EEPROM 256バイトに加え、Boot Row 256バイトもチップ消去の影響を受けます。`SYSCFG0.EESAVE=0` には `--allow-eeprom-erase`、`BROWSAVE=0` には `--allow-bootrow-erase` が必要です。後者は AVR64DU32 の消去操作専用で、ATtiny202 や `--no-chip-erase` との併用は拒否します。許可が足りなければ消去前に停止します。

消去前後の EEPROM と Boot Row を読み、保持設定なら一致、消去設定なら全バイト `FF` を確認します。32ビットのロック解除値も確認します。Flash 全領域の消去検証だけは `--verify-erase-flash` 指定時に行います。Boot Row / User Row の独立した読み書きコマンドはありません。

NVM v4 では ATtiny202 のページバッファ操作を使いません。FLWR（`02`）で書き込みを有効にし、Flash に16ビットずつ書き、状態を確認して NOCMD（`00`）で解除します。Fuse は EEERWR（`13`）、チップ消去は CHER（`20`）です。エラー時も NOCMD による解除を試み、消去・書き込みは再試行しません。

### AVR64DU32 の Fuse・ロック

書き込み可能な Fuse 名は `WDTCFG`、`BODCFG`、`OSCCFG`、`SYSCFG0`、`SYSCFG1`、`CODESIZE`、`BOOTSIZE` です。`APPEND` / `BOOTEND` は ATtiny202 用で、AVR64DU32 には使いません。`CODESIZE` / `BOOTSIZE` は512バイト単位で検証します。AVR64DU32 の予約ビットはデータシートに従って0を要求します。

```bash
./myapp -p AVR64DU32 --write-fuse SYSCFG1 09 --confirm-fuse
./myapp -p AVR64DU32 --lock --confirm-lock
```

`SYSCFG0.UPDIPINCFG` は1を要求し、UPDI 無効化を拒否します。`PDICFGL` / `PDICFGH` は `--read-fuses` で表示しますが、書き込みは拒否します。PDICFG の NVM 保護とロックを組み合わせると UPDI からの消去もできなくなるため、ロック操作は PDICFG が既定値 `0x0003` の場合だけ許可します。

AVR64DU32 のロック解除値は `0x5CC5C55C`、設定するロック値は `0xA33A3AA3` です。4バイトをリトルエンディアンで書き込み、読み戻しとリセット後の LOCKSTATUS を確認します。`--read-lock` は8桁の16進数で表示します。ロック済みデバイスの消去・解除は両デバイスとも非対応です。

AVR64DU32 は PTY テストによる検証のみで、実機未検証です。以下で ATtiny202 固有のサイズ・アドレス・NVM コマンドを記載している箇所は、この節の AVR64DU32 の値とは区別してください。

## 必要な環境

- Linux
- C++17 対応の `g++` と GNU Make
- テストを実行する場合は Python 3（標準ライブラリのみ使用）
- SerialUPDI 回路を備えた USB–UART アダプターと、電源を供給した対象チップ
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
| `2` | 読み出したシグネチャが 選択したデバイスの期待値と不一致 |

## 通信処理

1. シリアルポートを 115200 bps・8E2（8ビット、偶数パリティ、2ストップビット）、フロー制御なしで開きます。
2. UPDI リンクを初期化します。初回接続に失敗した場合は、300 bps・8E1 で double BREAK を送信して再試行します。
3. 32バイトの SIB を取得し、選択したデバイスのファミリーと NVM バージョン（ATtiny202 は `tinyAVR` / `0`、AVR64DU32 は `AVR` / `4`）を確認します。`P:0` のようなフィールドのコロン後を判定します。
4. NVM プログラミングキーを送信し、プログラミングモードへの移行を確認します。
5. 選択したデバイスのシグネチャ先頭アドレスから3バイトを読み出します。
6. リセット要求・解除と UPDI 無効化を行い、ポート設定の復元を試みて閉じます。

部分的な読み書き、送信エコーの照合、タイムアウトに対応しています。送信とエコー受信の共通期限、および応答受信の期限はそれぞれ1秒です。モード移行のポーリング期限は500ミリ秒ですが、個々の通信待ちにより全体ではそれを超える場合があります。

対象は ATtiny202 / NVM v0 と AVR64DU32 / NVM v4 です。すべての追加操作で SIB とシグネチャを確認し、不一致の場合は書き込みません。EEPROM の独立した読み書きコマンドは対象外です。ロック状態ではエラーになり、自動消去しません。読み出し操作でもターゲットはリセットされます。

### 連続読み出し

ATtiny202 では Flash の各64バイトページ（通常読み出し、HEX 出力、書き込み前確認、読み戻し検証、消去検証）と、消去前後の EEPROM 64バイトを連続読み出しします。chip erase 後の Flash 全2048バイト検証は `--verify-erase-flash` 指定時だけ行い、EEPROM の保持／消去の検証は常に行います。シグネチャ・Fuse・ステータスは従来どおり単バイトで読み出します。

UPDI の `ST ptr`（`55 69 addr_lo addr_hi`）で16ビットのバイトアドレスを設定し、エコーと `40` ACK を確認します。続いて `REPEAT`（`55 A0 count-1`）、`LD ptr++`（`55 24`）で指定バイト数を受信します。REPEAT と LD にターゲット ACK はありませんが、両方の送信エコーを従来どおり検証します。受信直前の flush は行いません。

内部の `readBlock` は選択した機種の16/24ビットアドレスに対応し、0バイトなら通信せず、1バイトなら REPEAT を省略します。256バイトを上限に分割し、ブロックごとに pointer を再設定します。選択したアドレス幅の範囲を超える要求は送信前に拒否します。各ブロックの応答全体に固定の1秒期限を適用し、部分受信が続いても期限を延長しません。ACK 拒否、エコー不一致、応答不足はエラーにし、不完全な結果を呼び出し元へ返しません。既存の終了処理を試みますが、通信が乱れた場合の終了処理成功までは保証できません。

通信量の理論比較（正常読み出し、NVM STATUS 確認・初期化・終了処理を除外）：

| 対象 | 送信処理回数：従来 → 連続読み出し | UPDI 線上のバイト数：従来 → 連続読み出し |
| --- | --- | --- |
| 64バイト（Flash 1ページ／EEPROM 全体1回） | 64 → 3 | 320 → 74 |
| Flash 全2048バイト（32ページ） | 2048 → 96 | 10240 → 2368 |

64バイトでは送信9バイト、ACK 1バイト、データ64バイトで合計74バイトです。送信処理回数を約95.3%、線上のバイト数を約76.9%削減します。送信エコーは同じ線上の送信を RX で観測するため、線上の時間として二重計上しません。115200 bps・8E2 の12ビット／文字だけで計算すると、Flash 全体の転送時間は約1.067秒から約0.247秒です。これは guard time、inter-byte delay、USB/UART の待ち、NVM 処理時間、OS の実行遅延を含まない理論値であり、実測の高速化率ではありません。実機の連続読み出しと所要時間は未検証です。

## Flash ページと Intel HEX

従来の引数なし実行、位置引数のポート指定、`-v`、ヘルプ、シグネチャ出力と終了コードは維持します。追加コマンドは1回の起動につき1つだけ指定できます。ファイル入力と引数の検証はポートを開く前に行います。読み出し先は**新規ファイル**に限定し、既存のバックアップやシンボリックリンクを上書きしません。

```bash
./myapp /dev/ttyUSB0 --read-page 0 page0.bin
./myapp /dev/ttyUSB0 --write-page 0 page0.bin
./myapp /dev/ttyUSB0 --read-hex backup.hex
# 既定は chip erase 後に書き込み（EESAVE=1 の場合）
./myapp /dev/ttyUSB0 --write-hex firmware.hex
# EESAVE=0 で EEPROM も消してよい場合
./myapp /dev/ttyUSB0 --write-hex firmware.hex --allow-eeprom-erase
# 消去せず書き込む場合
./myapp /dev/ttyUSB0 --write-hex firmware.hex --no-chip-erase
```

以下のサイズとアドレスは ATtiny202 の場合です。ページ番号は十進数または `0x` 付き16進数の 0～31、バイナリは厳密に64バイトです。Flash は2048バイト。HEX アドレスは Flash 先頭基準の **バイト単位** `0x0000`～`0x07FF` で、UPDI のシステムバスでは `0x8000` を加えます。HVPP のワードアドレスは使いません。Flash バッファはリトルエンディアンの16ビット STS でロードし、アドレス・データそれぞれの ACK を検証します。

HEX はデータ（00）、EOF（01）、拡張セグメント（02）、拡張リニアアドレス（04）に対応します。長さ、チェックサム、範囲、EOF を検査し、重複アドレス（同値を含む）、EOF 後のレコード、開始アドレス（03/05）などの未対応レコードを拒否します。空行・CRLF は許容しますが、データのない入力は拒否します。出力は全領域を16バイトレコードで保存し、空の Flash も往復可能です。

**`--write-hex` は既定で chip erase を行ってから書き込みます。** HEX 全体の入力検証をポートを開く前に完了し、対象チップと EESAVE を確認してから消去します。入力が一部のページだけでも、既存内容と完全一致でも、選択したデバイスの Flash 全領域を消去するため未選択ページの既存内容も失われます。EESAVE=0 なら `--allow-eeprom-erase` が必要で、指定がなければ消去・書き込み前にエラーにします。消去なしへ自動で切り替えることはありません。

`--write-hex --no-chip-erase` と `--write-page` は消去しません。この場合は未選択ページを変更しません。どちらの消去方針でも、ページ書き込み前にデータのある全選択ページを読み、ページ全体が `FF` または入力と完全一致の場合だけ許可します。HEX の選択ページ内の未指定バイトは `FF` 埋めです。完全一致ページは再書き込みせず、ATtiny202 で変更するページはバッファクリア（04）→ワード転送→ページ書き込み（01）→全64バイトの読み戻し比較を行います。後方のページが事前確認で不適合なら、前方のページも書き込みません。ただし既定の自動消去はこのページ事前確認より前に完了しています。

互換性の変更：以前の `--write-hex` の消去なし動作には、今後 `--no-chip-erase` の指定が必要です。`--confirm-erase` は廃止し、指定すると引数エラーになります。`--confirm-fuse` と `--confirm-lock` の条件は変更していません。

## チップ消去

```bash
./myapp /dev/ttyUSB0 --chip-erase
# EESAVE=0 のデバイスで EEPROM も消してよい場合だけ指定
./myapp /dev/ttyUSB0 --chip-erase --allow-eeprom-erase
# Flash 全2048バイトの読み戻し検証も行う場合
./myapp /dev/ttyUSB0 --chip-erase --allow-eeprom-erase --verify-erase-flash
```

**Flash 全領域を消去します。** ATtiny202 は NVMCTRL の CHIP ERASE（05）、AVR64DU32 は CHER（20）を使用します。**消去後の Flash 全領域読み戻し検証はデフォルト無効（false）**です。`--verify-erase-flash` を指定した場合だけ、選択したデバイスの全 Flash ページが `FF` であることを連続読み出しで確認します。このオプションは単独の `--chip-erase` と、既定の自動消去を行う `--write-hex` に適用できます。

オプション化する「全領域」は **chip erase 後の Flash 全領域**だけを意味します。EEPROM（ATtiny202 は64バイト、AVR64DU32 は256バイト）の保持／消去検証と各機種のロック解除値の確認、AVR64DU32 の Boot Row 検証、NVM 完了待ち・WRERROR 検査は常に行います。通常の Flash 書き込み前確認・書き込み後検証も変更しません。デフォルトの成功終了は Flash の全バイトを読み戻して消去確認したことを意味しません。

単独消去・HEX 書き込み時の自動消去とも、EESAVE=0 なら EEPROM も消えるため、`--allow-eeprom-erase` がなければ消去コマンド発行前に拒否します。EESAVE=1 なら EEPROM の消去前内容と消去後内容を比較します。保護を外す目的で Fuse を自動変更しません。

`--no-chip-erase` は `--write-hex` 専用です。`--allow-eeprom-erase` と `--verify-erase-flash` は消去を行う場合だけ指定でき、`--no-chip-erase` や他のコマンドとの併用はポートを開く前に拒否します。`--chip-erase` と `--write-hex` は別コマンドなので同時指定できません。消去の検証・完了待ちに失敗した場合は、その後の Flash 書き込みを行いません。

ロック済みデバイスではシグネチャや EESAVE を読めず、ATtiny202 と確認できません。そのため今回の実装は**ロック済みデバイスの消去／解除には非対応**です。CHIPERASE キーを送信する機能もありません。ロック後の復旧には、対象を確認して明示消去できる別のプログラマーが必要です。

## Fuse とロック

```bash
./myapp --read-fuses
./myapp --read-lock
./myapp --write-fuse SYSCFG1 FF --confirm-fuse
# バックアップと Flash の検証を済ませ、保護が必要な場合だけ実行
./myapp --lock --confirm-lock
```

ATtiny202 の Fuse は `WDTCFG`、`BODCFG`、`OSCCFG`、`SYSCFG0`、`SYSCFG1`、`APPEND`、`BOOTEND` の名前で指定します。値は16進数（例 `FF` または `0xFF`）です。旧 AVR の low/high/extended は受け付けません。予約オフセットはアクセス対象にせず、予約ビットは1を要求し、予約フィールド値・Flash を超える区画設定を拒否します。`SYSCFG0.RSTPINCFG` は UPDI（01）だけ許可し、GPIO／RESET への変更は明示指定があっても拒否します。

**Fuse はリセット後のクロック、BOD、WDT、CRC、起動時間、Flash 区画などを変更します。** 供給電圧とファームウェアに合う値をデータシートで確認してください。変更は1バイトずつ WFU（07）で行い、読み戻し比較後に終了処理でリセットします。既存値と同一なら書き込みません。

ATtiny202 のロックは任意の数値を受け付けず `3A` の設定に限定します。`C5` が解除状態であることを確認後、書き込み・読み戻し・リセット後の LOCKSTATUS を検証します。ロック後はメモリを読み出せません。ロック解除のため `C5` を書き込むコマンドや自動消去はありません。検証に失敗してもロックは既に有効になっている可能性があります。

## 失敗時と実機未検証事項

NVM STATUS の各機種の Flash/EEPROM busy ビット を期限付き（2秒）で監視し、WRERROR、ACK 不一致、エコー不一致、読み戻し不一致、タイムアウトで停止します。個々の通信にも既存の1秒期限があり、全体の待ち時間がポーリング期限を超える場合があります。リンク確立後は失敗時も終了処理を試み、元のエラーを残します。消去・書き込みコマンドを自動再試行しません。

複数ページの書き込みはトランザクションではありません。途中の通信断／電源断では、前のページだけが書かれたり、消去・Fuse・ロックの結果が不確定になったりします。自動ロールバックはしません。終了処理のリセットにより、不完全なプログラムが動き始める可能性もあります。失敗後は装置を安全な状態にしてから、読み出しと比較で状態を確認してください。

以前のシグネチャ取得以外の追加機能は**実機未検証**です。PTY は通信列とソフトウェアの判断を検証しますが、実際の NVM 書き込み完了、電圧・波形、電源断耐性、Fuse の再起動時動作、ロックの実際の発効は保証しません。実機ではバックアップ後に、Flash 読み出し→空ページへの書き込み／検証→明示消去と EEPROM 保持→安全な Fuse→最後にロックの順で確認が必要です。

## テスト

```bash
make test
```

Python の疑似端末（PTY）でターゲットの応答を模擬します。実機は不要です。正常な読み出しと受信の分割、シグネチャ不一致、不正・未対応の SIB、キー拒否、エコー不一致、ロック状態、応答タイムアウト、double BREAK による復帰を確認します。

AVR64DU32 の SIB / シグネチャ、24ビットアドレスと境界、最終ページの読み書き、64 KB の HEX 入出力、NVM v4 コマンドとエラー、Boot Row 保護、32ビットロックを検証します。

HEX 書き込みの既定自動消去、EESAVE=0 の拒否／許可と EEPROM 消去、消去なしの全選択ページ事前確認、消去検証失敗後の書き込み停止、不正 HEX の接続前拒否、オプションの組み合わせも検証します。

PTY テストでは実際の電気的特性や UART のパリティ・ストップビットの動作は検証できません。通信設定やコマンド列を変更した場合は、実機でも確認してください。

## Makefile の主なターゲット

| ターゲット | 内容 |
| --- | --- |
| `make` | `myapp` をビルド |
| `make run` | ビルド済みの `myapp` をデフォルト設定で実行 |
| `make test` | ビルド後、疑似端末テストを実行 |
| `make query` | 対象を識別し通信ログを表示（`PART`・`PORT` を指定可能） |
| `make chiperase` | 選択デバイスを全消去（`ERASE_OPTIONS` で消去許可・検証を指定） |
| `make writehex` | `HEX` のファイルを全消去後に書き込み |
| `make dude` | 比較用の avrdude コマンドを実行（avrdude が必要） |
| `make clean` | 実行ファイル、オブジェクト、依存関係ファイルを削除 |
| `make install` | ビルド済みの `myapp` を `~/.local/bin/` に配置 |
| `make uninstall` | `~/.local/bin/myapp` を削除 |

`query`・`chiperase`・`writehex` は実行前にビルドします。`PART` の既定値は `ATtiny202`、`PORT` は `/dev/ttyUSB0`、`ERASE_OPTIONS` は `--allow-eeprom-erase` です。`HEX` の既定値は ATtiny202 の場合だけ `example/attiny202blink.hex` とし、それ以外は明示指定を要求します。

```bash
make query PART=AVR64DU32
make writehex PART=AVR64DU32 HEX=firmware-du.hex ERASE_OPTIONS="--allow-eeprom-erase --allow-bootrow-erase"
```

`run` と `install` はビルドを行わないため、先に `make` を実行してください。`install` の配置先ディレクトリは事前に用意する必要があります。

## 問題が起きた場合

- ポートを開けない場合は、デバイスパスとアクセス権、ほかのアプリケーションによる使用状況を確認してください。
- エコー不一致やタイムアウトの場合は、SerialUPDI 回路、配線、電源、GND、信号電圧を確認してください。
- `unsupported SIB` の場合は、エラーに表示された SIB と `-v` の受信ログを確認してください。
- avrdude と比較する場合は、同じ回路とポートで `make dude` を実行してください。同時に両プログラムを起動しないでください。

## ファイル構成

- `main.cpp`：シリアル通信、UPDI 制御、コマンドライン処理
- `device.h`：機種名、シグネチャ、メモリ形状、Fuse の機種別定義
- `nvm.h`：NVM v0 / v4 操作、事前確認、読み戻し検証
- `image.h`：選択機種に応じた Intel HEX、Fuse 名と値の検証
- `main.h`：現時点では未使用のヘッダーファイル
- `Makefile`：ビルド・実行・テスト
- `tests/test_serialupdi.py`：既存のリンク／シグネチャ通信テスト
- `tests/test_avr64du32.py`：AVR64DU32 の通信・NVM・HEX・Fuse・ロック・消去保護の PTY テスト
- `tests/test_nvm.py`：NVM、Flash、HEX、Fuse、ロック、安全な拒否の PTY テスト
- `tests/test_block_read.py` / `tests/block_read.cpp`：実 UPDI／シリアル層の連続読み出し境界・期限の PTY テスト（一時ディレクトリにテスト実行ファイルをビルド）
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

- 連続読み出しは ATtiny202 データシートの [LD](https://onlinedocs.microchip.com/oxy/GUID-5A56DB3A-31E1-4F46-984F-39186535C84E-en-US-7/GUID-22EC0B04-1A88-4D08-A69D-B85D39FA09AE.html)、[ST](https://onlinedocs.microchip.com/oxy/GUID-5A56DB3A-31E1-4F46-984F-39186535C84E-en-US-7/GUID-530B9975-690F-48EE-9EA6-64F2F21ABF96.html) と Microchip の [pymcuprog link.py](https://github.com/microchip-pic-avr-tools/pymcuprog/blob/main/pymcuprog/serialupdi/link.py) の `st_ptr`、`repeat`、`ld_ptr_inc` に照合しました。オンライン版の REPEAT 節リンクは取得時404だったため、カウント符号化はメーカー実装でも確認しています。

### AVR64DU32 対応で参照した資料

- [Microchip AVR64DU28/32 データシート DS40002548A](https://ww1.microchip.com/downloads/aemDocuments/documents/MCU08/ProductDocuments/DataSheets/AVR64DU-28-32-Prelim-DataSheet-DS40002548.pdf)：8章（メモリ、Fuse、ロック）、11章（NVMCTRL）、33章（UPDI）。
- [Microchip の AVR64DU32 定義](https://github.com/microchip-pic-avr-tools/pymcuprog/blob/main/pymcuprog/deviceinfo/devices/avr64du32.py) と [NVM P:4 実装](https://github.com/microchip-pic-avr-tools/pymcuprog/blob/main/pymcuprog/serialupdi/nvmp4.py)：形状、24ビットアドレス、コマンド順序、Fuse の EEPROM 書き込み方式を確認。
- Microchip 提供の `ioavr64du32.h`：Fuse のマスク、値、32ビットロックキーを確認。
