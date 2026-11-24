/*  QZip-mcm fork: delimiter-aware structured-data (CSV/TSV) context tracker.

  Copyright (C) 2026, Elliott Hankinson (QZip).
  This file is part of a fork of the MCM file compressor by Mathieu Chartier
  (Copyright (C) 2013, Google Inc.) and is distributed under the same license:

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.

  The tracker is fully adaptive: the delimiter and the "table mode" flag are
  derived only from bytes already coded, so the encoder and decoder agree
  without any extra header bits.
*/

#ifndef _QZ_CSV_HPP_
#define _QZ_CSV_HPP_

#include <cstdint>
#include <cstring>

class QzCsvTracker {
public:
  static const uint32_t kMaxFields = 256;
  static const uint32_t kMaxLine = 1u << 16;

  QzCsvTracker() { Reset(); }

  void Reset() {
    delim_ = 0; active_ = false; stable_ = 0; miss_ = 0; last_n_ = 0;
    fi_ = 0; fpos_ = 0; col_ = 0; in_quote_ = false; prev_byte_ = 0;
    cur_hash_ = 0; last_field_hash_ = 0; cur_len_ = 0; prev_len_ = 0;
    std::memset(cand_count_, 0, sizeof(cand_count_));
    std::memset(prev_hash_, 0, sizeof(prev_hash_));
    std::memset(row_hash_, 0, sizeof(row_hash_));
    std::memset(prev_start_, 0, sizeof(prev_start_));
    std::memset(cur_start_, 0, sizeof(cur_start_));
    prev_nf_ = 0;
    lines_ = 0; ever_active_ = false;
  }

  // c is the raw (un-reordered) byte just coded.
  inline void Update(uint8_t c) {
    bool sep = false, eol = false;
    if (c == '\n') {
      if (!in_quote_ || prev_byte_ == '"') { eol = true; in_quote_ = false; }
    } else if (c == '"') {
      if (fpos_ == 0) in_quote_ = true;
    } else if (!in_quote_ || prev_byte_ == '"') {
      if (c == ',' || c == '\t' || c == ';' || c == '|') {
        ++cand_count_[CandIdx(c)];
        if (c == delim_) { sep = true; in_quote_ = false; }
      }
    }
    if (cur_len_ < kMaxLine) cur_line_[cur_len_++] = c;
    prev_byte_ = c;
    ++col_;
    if (eol) {
      EndField();
      EndLine();
    } else if (sep) {
      EndField();
    } else {
      cur_hash_ = (cur_hash_ + c + 1) * 0x2F0F1E35u;
      ++fpos_;
    }
  }

  void SetParams(uint32_t min_fields, uint32_t stable_need, uint32_t miss_limit) {
    min_fields_ = min_fields; stable_need_ = stable_need; miss_limit_ = miss_limit;
  }
  bool Active() const { return active_; }
  // A block that has not looked like a table within its first probe_lines_ lines stops being tracked.
  bool Dead() const { return probe_lines_ != 0 && !ever_active_ && lines_ >= probe_lines_; }
  void SetProbe(uint32_t lines) { probe_lines_ = lines; }
  uint32_t Field() const { return fi_ < kMaxFields ? fi_ : kMaxFields - 1; }
  uint32_t FieldPos() const { return fpos_; }
  uint32_t FieldHash() const { return cur_hash_; }
  uint32_t LastFieldHash() const { return last_field_hash_; }
  uint32_t AboveFieldHash() const { return prev_hash_[Field()]; }
  // Byte at the same offset of the same field in the previous row (256 = field ended there, 257 = none).
  uint32_t AboveFieldByte() const {
    const uint32_t f = Field();
    if (f >= prev_nf_) return 257;
    const uint32_t start = prev_start_[f];
    const uint32_t end = (f + 1 < prev_nf_) ? prev_start_[f + 1] - 1 : prev_len_ - 1;
    const uint32_t p = start + fpos_;
    if (p < end && p < prev_len_) return prev_line_[p];
    return 256;
  }
  // Byte at the same column (fixed width) in the previous row.
  uint32_t AboveColByte() const { return col_ < prev_len_ ? prev_line_[col_] : 256; }
  uint32_t Col() const { return col_; }

private:
  static inline int CandIdx(uint8_t c) { return c == ',' ? 0 : c == '\t' ? 1 : c == ';' ? 2 : 3; }

  inline void EndField() {
    const uint32_t f = Field();
    row_hash_[f] = cur_hash_;
    last_field_hash_ = cur_hash_ + f * 0x9E3779B1u;
    cur_hash_ = 0; fpos_ = 0;
    ++fi_;
    if (fi_ < kMaxFields) cur_start_[fi_] = cur_len_;
  }

  inline void EndLine() {
    // Choose the delimiter for the next line from this line's counts (adaptive, decoder-safe).
    int best = 0;
    for (int i = 1; i < 4; ++i) if (cand_count_[i] > cand_count_[best]) best = i;
    const uint32_t n = cand_count_[best];
    static const uint8_t kCands[4] = { ',', '\t', ';', '|' };
    const uint8_t d = n ? kCands[best] : 0;
    // Field count observed with the delimiter in effect for this line.
    const uint32_t nf = fi_;
    if (d != 0 && d == delim_ && nf >= min_fields_ && nf == last_n_) {
      if (stable_ < 255) ++stable_;
      miss_ = 0;
    } else {
      if (miss_ < 255) ++miss_;
      stable_ = 0;
    }
    if (!active_ && stable_ >= stable_need_) active_ = ever_active_ = true;
    ++lines_;
    if (active_ && miss_ >= miss_limit_) active_ = false;
    if (d != 0 && d != delim_ && (!active_ || miss_ >= 2)) delim_ = d;
    last_n_ = nf;
    std::memset(cand_count_, 0, sizeof(cand_count_));
    // Rotate line buffers.
    std::memcpy(prev_line_, cur_line_, cur_len_);
    prev_len_ = cur_len_;
    prev_nf_ = nf < kMaxFields ? nf : kMaxFields;
    std::memcpy(prev_start_, cur_start_, sizeof(uint32_t) * prev_nf_);
    std::memcpy(prev_hash_, row_hash_, sizeof(uint32_t) * prev_nf_);
    for (uint32_t i = prev_nf_; i < kMaxFields && prev_hash_[i]; ++i) prev_hash_[i] = 0;
    cur_len_ = 0; fi_ = 0; fpos_ = 0; col_ = 0; cur_hash_ = 0; last_field_hash_ = 0;
    cur_start_[0] = 0;
  }

  uint32_t min_fields_ = 2, stable_need_ = 2, miss_limit_ = 8;
  uint32_t probe_lines_ = 0, lines_ = 0;
  bool ever_active_ = false;
  uint8_t delim_;
  bool active_;
  uint32_t stable_, miss_, last_n_;
  uint32_t fi_, fpos_, col_;
  bool in_quote_;
  uint8_t prev_byte_;
  uint32_t cur_hash_, last_field_hash_;
  uint32_t cand_count_[4];
  uint32_t prev_hash_[kMaxFields], row_hash_[kMaxFields];
  uint32_t prev_start_[kMaxFields], cur_start_[kMaxFields];
  uint32_t prev_nf_;
  uint32_t cur_len_, prev_len_;
  uint8_t cur_line_[kMaxLine], prev_line_[kMaxLine];
};

#endif
