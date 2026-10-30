# Hasil perbaikan review Bagian 2

Tanggal: 30 Oktober 2026. Pengguna mengizinkan perbaikan temuan review.
Mode AFTER dipertahankan. Laporan awal tetap disimpan sebagai riwayat.

## Status tujuh temuan

1. **Akses static: diperbaiki.** Path di-decode dan di-resolve, kemudian wajib
   berada di dalam direktori static. Tes jalur traversal biasa, encoded, dan
   backslash menghasilkan 403/404; file poster tetap dapat dibaca.
2. **Cleanup SSE: diperbaiki.** Worker gRPC terpisah memasukkan event ke antrean
   maksimal 64. Handler HTTP memeriksa socket dan mengirim heartbeat tiap 0,5
   detik saat idle. Disconnect/shutdown membatalkan channel dan menutup worker.
   Tes tiga kali subscribe-disconnect tanpa booking membuktikan subscriber
   kembali nol. Tes shutdown pada stream idle juga lulus.
3. **Timeout nyata: ditambahkan.** `scripts/demo_fault_tolerance.py --local`
   membuka server nyata dan DB sementara. Balasan pertama ditunda setelah commit
   selama 0,6 detik; timeout client 0,15 detik. Callback menerima
   DEADLINE_EXCEEDED, UUID tetap sama, dan database hanya memiliki satu booking.
   Delay hanya ada di helper demo, tidak ditambahkan ke server utama.
4. **State UUID web: diperbaiki.** Respons final REJECTED menyelesaikan operasi
   dan menghasilkan UUID baru. Pada timeout atau koneksi putus, payload dan UUID
   lama disimpan; tombol retry mengirim operasi identik. Perubahan jadwal/nama/
   kursi/UUID ditahan selama hasil belum diketahui. Klik ganda tidak mengirim
   dua panggilan paralel. Tes mengeksekusi fungsi JavaScript asli dari HTML.
5. **Kebenaran demo: diperbaiki.** Demo memilih kursi tersedia, memeriksa respons
   serta update target, dan keluar dengan kode 1 saat gagal. Dua kali demo pada
   DB sama lulus dengan kursi berbeda. Tanpa kursi tersedia, tidak ada keluaran
   sukses. Mode eksternal menjelaskan bahwa pengulangan manual bukan timeout.
   Tombol demo web juga tidak menganggap hasil gagal sebagai sukses.
6. **Versi Docker: diperbaiki pada konfigurasi.** Kedua Dockerfile kini memakai
   python:3.14-slim. Build/run belum diuji karena Docker tidak tersedia.
7. **Keyboard film/kursi: diperbaiki.** Div interaktif diganti button native
   dengan label, selected state, dan focus outline. Pemilihan film serta kursi
   melalui Enter berhasil diuji langsung di browser. Label input terhubung,
   contoh nama bawaan dihapus, dan motion mengikuti prefers-reduced-motion.

## Bukti pengujian akhir

- PASS, regresi Python: **33 test lulus dalam 12,632 detik** pada Python 3.14.6.
- PASS, state web: `node tests/test_web_state.js`; UUID setelah REJECTED,
  payload/ID tetap pada gangguan, dan klik ganda diperiksa.
- PASS, dependency: `pip check` menghasilkan `No broken requirements found.`
- PASS, sintaks Python: compileall server/client/web/scripts/tests berhasil.
- PASS, whitespace patch: `git diff --check` tanpa error. Peringatan konversi
  LF/CRLF dari Git bukan kegagalan pemeriksaan.
- PASS, browser: memilih Misteri Kota dengan Enter, memilih A1 dengan Enter,
  memasukkan nama, booking sukses, update BOOKED, UUID baru; tombol deduplikasi
  mengembalikan booking ID sama; race menghasilkan satu CONFIRMED dan empat
  REJECTED; benchmark menampilkan hasil 1/5/20 client.
- Screenshot pemeriksaan: `.tmp/web-polish.png` (file sementara, diabaikan Git).

## Batas verifikasi

Semua pengujian memakai database sementara, termasuk browser di port uji.
Server pemeriksaan dan tab browser sudah ditutup. Database utama tidak dipakai
untuk pengujian. Tidak ada commit atau push yang dilakukan.

README dan PETUNJUK_DEMO telah diperbarui. Angka benchmark lama yang tidak
disertai log mentah diberi catatan agar tidak dipakai sebagai hasil terverifikasi.
Demo dua komputer/container, video kelompok, serta build Docker tetap perlu
dikerjakan di lingkungan yang sesuai. Ini verifikasi perbaikan fungsi yang
diminta, bukan klaim audit menyeluruh seluruh desain visual atau WCAG.
