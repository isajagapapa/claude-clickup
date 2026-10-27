# claude-clickup

[English](README.md) · **Bahasa Indonesia**

Skill [Claude Code](https://claude.com/claude-code) untuk membaca task ClickUp dari link-nya. Tempel satu atau banyak link card, lalu Claude mengambil detailnya lewat ClickUp API, merangkumnya, dan membuat rencana kerja sesuai tipe card (bug, feature, enhancement, ...) di project yang sedang Anda kerjakan.

Link ClickUp butuh login, jadi Claude tidak bisa membukanya dengan mengambil halaman web biasa. Karena itu skill ini memakai personal API token Anda.

## Fitur

- Baca banyak link sekaligus, diambil paralel
- Judul, status, prioritas, assignee, tanggal mulai/tenggat, tag, folder dan list
- Deskripsi (Markdown), checklist, subtask, nama lampiran, komentar terbaru
- Task ID biasa dan custom task ID (`PROJ-123`)
- Mengenali tipe card dari tag atau prefix judul (bug, feature, enhancement, refactor, research, docs, chore)
- Skill `plan`: rencana kerja per card sesuai tipenya; di plan mode hanya membuat rencana, di mode lain membuat rencana lalu langsung mengeksekusi
- Read-only: tidak pernah mengubah apa pun di ClickUp
- Hanya pakai standard library Python, tidak perlu install package

## Persyaratan

- Claude Code
- Python 3.9 atau lebih baru (`python3 --version`)
- Akun ClickUp yang punya akses ke task yang ingin dibaca

## 1. Ambil API token ClickUp

1. Di ClickUp, klik avatar Anda → **Settings** → **Apps**.
2. Di bagian **API Token**, klik **Generate** (atau **Copy** kalau sudah punya). Token diawali `pk_`.

Token ini punya akses yang sama dengan akun Anda. Jaga kerahasiaannya.

## 2. Simpan token sebagai environment variable

Jangan tempel token ke chat dan jangan di-commit ke repository.

**macOS / Linux** (zsh adalah default macOS; pakai `~/.bashrc` untuk bash):

```bash
echo 'export CLICKUP_API_TOKEN="pk_token_anda"' >> ~/.zshrc
source ~/.zshrc
```

**Windows** (PowerShell atau Command Prompt):

```powershell
setx CLICKUP_API_TOKEN "pk_token_anda"
```

Setelah itu tutup lalu buka lagi terminal, editor (VS Code, JetBrains) dan Claude Code supaya variabel barunya terbaca.

Cek tanpa menampilkan token:

```bash
[ -n "$CLICKUP_API_TOKEN" ] && echo "token ada" || echo "token belum ada"
```

## 3. Instalasi

### Opsi A: plugin marketplace (disarankan)

Di dalam Claude Code:

```
/plugin marketplace add isajagapapa/claude-clickup
/plugin install clickup@claude-clickup
```

Untuk update nanti: `/plugin marketplace update claude-clickup`.

### Opsi B: copy manual

```bash
git clone https://github.com/isajagapapa/claude-clickup.git
mkdir -p ~/.claude/skills
cp -R claude-clickup/skills/clickup ~/.claude/skills/clickup
cp -R claude-clickup/skills/plan ~/.claude/skills/clickup-plan
sed -i.bak 's/^name: plan$/name: clickup-plan/' ~/.claude/skills/clickup-plan/SKILL.md && rm ~/.claude/skills/clickup-plan/SKILL.md.bak
```

Skill `plan` di-copy sebagai `clickup-plan` karena Claude Code sudah punya perintah bawaan `/plan`. Letakkan kedua folder bersebelahan: `clickup-plan` menjalankan script yang ada di `clickup`.

Kalau hanya ingin dipakai di satu project, copy ke `<project>/.claude/skills/`.

## Skills

| Skill | Perintah plugin | Install manual | Fungsi |
|---|---|---|---|
| `clickup` | `/clickup:clickup <link>` | `/clickup <link>` | Membaca card lalu merangkumnya |
| `plan` | `/clickup:plan <link>` | `/clickup-plan <link>` | Membuat rencana kerja sesuai tipe card, lalu mengeksekusi kalau bukan plan mode |

### `clickup`: baca card

Panggil skill lalu berikan link-nya:

```
/clickup:clickup https://app.clickup.com/t/86abc123
```

(Kalau install manual, perintahnya `/clickup`.)

Banyak link sekaligus, dipisah spasi, koma atau baris baru:

```
/clickup:clickup https://app.clickup.com/t/86abc123 https://app.clickup.com/t/86def456
https://app.clickup.com/t/9012345678/PROJ-42
```

Anda juga bisa langsung menempel link di pesan biasa, Claude akan memakai skill ini dengan sendirinya:

```
Card berikut isinya apa saja?
https://app.clickup.com/t/86abc123
https://app.clickup.com/t/86def456
```

Claude membalas dengan ringkasan per card (tujuan, acceptance criteria, keputusan dari komentar, pertanyaan terbuka), ditambah tabel ringkas (card, tipe, status, assignee, tenggat) kalau card-nya lebih dari satu.

### `plan`: rencanakan pekerjaan

```
/clickup:plan https://app.clickup.com/t/86abc123 https://app.clickup.com/t/86def456
```

Meminta lewat pesan biasa ("buatkan plan untuk card ini", "perbaiki card ini") juga memicunya. Untuk setiap card Claude:

1. membaca card dan menentukan tipenya dari tag (kalau tidak ada, dari prefix judul seperti `[Bug]` atau `fix:`, lalu dari penilaiannya sendiri yang ditulis sebagai asumsi),
2. menjelajahi project Anda supaya rencana menyebut file dan test yang nyata,
3. menulis rencana dari template tipe tersebut, ditambah pertanyaan terbuka untuk pemilik card,
4. menampilkannya di chat, dan juga menyimpannya ke lokasi plan yang diatur CLAUDE.md project Anda (kalau ada aturannya).

Langkah berikutnya tergantung mode Claude Code:

| Mode | Perilaku |
|---|---|
| Plan mode (Shift+Tab) | Hanya membuat rencana. Claude berhenti di persetujuan plan dan menunggu instruksi Anda. |
| Mode lain | Membuat rencana, memberi peringatan "⚡ Not in plan mode: executing this plan now", lalu langsung mengeksekusi. Tekan Esc untuk berhenti. |

Aturan kerja untuk setiap card:

- **Review sebagai senior developer**: arsitektur, edge case, error handling, keamanan, performa dan maintainability dicek sebelum rencana final. Solusi yang lemah di card diberi usulan yang lebih baik.
- **Best practice sesuai stack**: bahasa, framework dan versinya dibaca dari project Anda (`composer.json`, `package.json`, `pyproject.toml`, ...), lalu idiom, linter dan pola test-nya diikuti. Konvensi project Anda sendiri yang menang.
- **Scope yang jelas**: setiap rencana menyebut apa yang masuk scope dan apa yang di luar scope, dan eksekusi tidak keluar dari situ.
- **Rekomendasi**: temuan di luar scope yang memengaruhi scope (bug lain, tech debt, risiko keamanan) dicatat beserta dampak dan sarannya, tidak pernah diubah tanpa persetujuan Anda.
- **Self-fixing**: saat eksekusi, test dan linter dijalankan setelah tiap langkah. Kegagalan dan bug di dalam scope diperbaiki sampai hijau (tidak dengan melemahkan test); setelah 3 kali gagal Claude berhenti dan melapor.

Tipe card dan tag yang dipetakan ke sana (dicocokkan per kata, tidak peka huruf besar/kecil; kalau beberapa tag cocok, baris paling atas yang menang):

| Tipe | Tag | Fokus rencana |
|---|---|---|
| `bug` | bug, bugfix, fix, hotfix, defect, error, regression, incident, perbaikan | reproduce, root cause, test gagal dulu, fix minimal, rollback |
| `feature` | feature, feat, story, fitur | scope dan acceptance criteria, desain, tahapan, rollout |
| `enhancement` | enhancement, enhance, improvement, improve, optimization, performance, tweak, peningkatan | kondisi sekarang vs yang diinginkan, kompatibilitas, cara mengukur |
| `refactor` | refactor, cleanup, tech-debt | perilaku tidak berubah, test pengaman, langkah kecil yang tetap hijau |
| `research` | research, spike, investigation, poc, riset | pertanyaan, opsi, timebox, rekomendasi |
| `docs` | docs, documentation, dokumentasi | pembaca, file, keakuratan contoh |
| `chore` | chore, maintenance, infra, devops, config, deps, upgrade | perubahan, dampak, rollback |

Template-nya ada di [`skills/plan/references/`](skills/plan/references/).

### Format link yang didukung

| Input | Contoh |
|---|---|
| Link task | `https://app.clickup.com/t/86abc123` |
| Link custom task ID | `https://app.clickup.com/t/9012345678/PROJ-42` |
| Task ID saja | `86abc123` |
| Custom ID saja | `PROJ-42` (perlu `CLICKUP_TEAM_ID` berisi ID workspace Anda) |

Link list, board dan doc (`/v/li/...`, `/v/b/...`, `/v/dc/...`) bukan task. Buka task-nya lalu pakai **Copy link**.

### Jalankan script langsung

Skill ini memanggil script kecil yang juga bisa Anda jalankan sendiri:

```bash
python3 skills/clickup/scripts/fetch_tasks.py https://app.clickup.com/t/86abc123 --tz Asia/Jakarta
```

| Opsi | Arti |
|---|---|
| `--tz <zona>` | Zona waktu IANA untuk tanggal, mis. `Asia/Jakarta` (default: waktu lokal) |
| `--max-comments <n>` | Jumlah komentar terbaru per task, `-1` untuk semua (default: 20) |
| `--team-id <id>` | ID workspace untuk custom ID tanpa link (default: `$CLICKUP_TEAM_ID`) |
| `--json` | Output JSON terstruktur, bukan Markdown |

Exit code: `0` semua berhasil, `1` ada yang gagal, `2` salah pemakaian, `3` token belum diset.

## Troubleshooting

| Masalah | Solusi |
|---|---|
| `CLICKUP_API_TOKEN is not set` | Set variabelnya (langkah 2), lalu restart Claude Code dan editor sepenuhnya. |
| `HTTP 401: token is invalid or expired` | Generate token baru di ClickUp lalu perbarui variabelnya. |
| `HTTP 403` / `HTTP 404` | Pemilik token tidak punya akses ke task itu, task sudah dihapus, atau custom ID milik workspace lain. |
| `unsupported ClickUp link` | Itu link list/board/doc. Copy link task-nya sendiri. |
| `python3: command not found` di Windows | Install Python dari python.org, atau pakai `python` / `py`. Claude otomatis mencoba keduanya. |
| `unknown time zone` di Windows | Jalankan `pip install tzdata`, atau jangan pakai `--tz`. |

## Keamanan

- Token hanya dibaca dari environment variable `CLICKUP_API_TOKEN` dan tidak pernah ditampilkan.
- Skill ini hanya mengirim request `GET` ke `api.clickup.com`. Tidak bisa membuat, mengubah atau menghapus apa pun.
- Isi card diperlakukan sebagai data, bukan instruksi untuk Claude.
- Siapa pun yang memegang token Anda punya akses ClickUp Anda. Kalau bocor, segera generate ulang di ClickUp.

## Uninstall

- Plugin: `/plugin uninstall clickup@claude-clickup`, lalu `/plugin marketplace remove claude-clickup`
- Manual: `rm -rf ~/.claude/skills/clickup ~/.claude/skills/clickup-plan`

## Kontribusi

```bash
python3 -m unittest discover tests -v
```

Test berjalan offline dan tidak butuh token. Sertakan test di setiap perubahan.

## Lisensi

[MIT](LICENSE)
