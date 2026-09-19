// Native implementation of the byte-lossless EQ3TMK1 storage codec.
//
// This file intentionally has no simulator dependency.  The on-disk contract
// is defined by eq3_campaign_fieldcodec.py: a raw magic/header followed by one
// zlib stream containing literal (L) and temporal-delta (D) records.
#include <zlib.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <exception>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr std::string_view kMagic{"EQ3TMK1\n"};
constexpr std::size_t kChunk = 65536;

void require(bool condition, const std::string& message) {
  if (!condition) throw std::invalid_argument(message);
}

bool ascii_space(unsigned char value) {
  return value == ' ' || value == '\t' || value == '\n' || value == '\r' ||
         value == '\v' || value == '\f';
}

std::string_view stripped(std::string_view value) {
  while (!value.empty() && ascii_space(static_cast<unsigned char>(value.front())))
    value.remove_prefix(1);
  while (!value.empty() && ascii_space(static_cast<unsigned char>(value.back())))
    value.remove_suffix(1);
  return value;
}

void append_u32_le(std::vector<unsigned char>& output, std::uint32_t value) {
  for (unsigned shift = 0; shift != 32; shift += 8)
    output.push_back(static_cast<unsigned char>((value >> shift) & 0xffU));
}

std::uint32_t read_u32_le(const unsigned char* input) {
  return static_cast<std::uint32_t>(input[0]) |
         (static_cast<std::uint32_t>(input[1]) << 8) |
         (static_cast<std::uint32_t>(input[2]) << 16) |
         (static_cast<std::uint32_t>(input[3]) << 24);
}

void append_i32_le(std::vector<unsigned char>& output, std::int32_t value) {
  append_u32_le(output, static_cast<std::uint32_t>(value));
}

std::int32_t read_i32_le(const unsigned char* input) {
  return static_cast<std::int32_t>(read_u32_le(input));
}

std::string native_value(std::int32_t value) {
  const std::int64_t wide = value;
  const bool negative = wide < 0;
  const std::uint64_t magnitude = static_cast<std::uint64_t>(negative ? -wide : wide);
  std::string result = (negative ? "-" : "") + std::to_string(magnitude / 1000) + ".";
  const std::string fraction = std::to_string(magnitude % 1000);
  result.append(3 - fraction.size(), '0');
  result += fraction;
  if (result.size() < 7) result.insert(result.begin(), 7 - result.size(), ' ');
  result += "  ";
  return result;
}

std::int32_t parse_token(std::string_view token) {
  require(!token.empty(), "empty numeric token");
  std::size_t position = 0;
  bool negative = false;
  if (token[position] == '-') {
    negative = true;
    ++position;
  }
  const std::size_t whole_begin = position;
  while (position < token.size() && token[position] >= '0' && token[position] <= '9')
    ++position;
  require(position > whole_begin && position < token.size() && token[position] == '.',
          "not a finite three-decimal canonical numeric row");
  const std::size_t dot = position++;
  require(token.size() - position == 3, "not a finite three-decimal canonical numeric row");
  for (std::size_t index = position; index < token.size(); ++index)
    require(token[index] >= '0' && token[index] <= '9',
            "not a finite three-decimal canonical numeric row");

  std::uint64_t whole = 0;
  for (std::size_t index = whole_begin; index < dot; ++index) {
    const unsigned digit = static_cast<unsigned>(token[index] - '0');
    require(whole <= (static_cast<std::uint64_t>(std::numeric_limits<std::int32_t>::max()) +
                      static_cast<unsigned>(negative) - digit) /
                         10,
            "milli-K outside int32 range");
    whole = whole * 10 + digit;
  }
  const std::uint64_t fraction =
      static_cast<unsigned>(token[position] - '0') * 100U +
      static_cast<unsigned>(token[position + 1] - '0') * 10U +
      static_cast<unsigned>(token[position + 2] - '0');
  require(whole <= (std::numeric_limits<std::uint64_t>::max() - fraction) / 1000,
          "milli-K outside int32 range");
  const std::uint64_t magnitude = whole * 1000 + fraction;
  const std::uint64_t limit = static_cast<std::uint64_t>(std::numeric_limits<std::int32_t>::max()) +
                              static_cast<unsigned>(negative);
  require(magnitude <= limit, "milli-K outside int32 range");
  const std::int64_t signed_value =
      negative ? -static_cast<std::int64_t>(magnitude) : static_cast<std::int64_t>(magnitude);
  return static_cast<std::int32_t>(signed_value);
}

std::vector<std::string_view> tokens(std::string_view value) {
  std::vector<std::string_view> result;
  std::size_t position = 0;
  while (position < value.size()) {
    while (position < value.size() && ascii_space(static_cast<unsigned char>(value[position])))
      ++position;
    if (position == value.size()) break;
    const std::size_t begin = position;
    while (position < value.size() && !ascii_space(static_cast<unsigned char>(value[position])))
      ++position;
    result.push_back(value.substr(begin, position - begin));
  }
  return result;
}

void file_write(std::FILE* file, const void* data, std::size_t size) {
  if (size && std::fwrite(data, 1, size, file) != size)
    throw std::runtime_error("failed writing codec file");
}

std::string system_error(const std::string& prefix) {
  return prefix + ": " + std::strerror(errno);
}

class Encoder {
 public:
  Encoder(const char* path, std::uint32_t nx, std::uint32_t ny, std::uint32_t frames,
          int level)
      : nx_(nx), ny_(ny), frames_(frames) {
    require(path && *path, "codec path is required");
    require(nx_ && ny_ && frames_ &&
                static_cast<std::uint64_t>(nx_) * ny_ <= 400000 && level >= 0 && level <= 9,
            "invalid codec dimensions/level");
    file_ = std::fopen(path, "wbx");
    if (!file_) throw std::runtime_error(system_error("cannot create codec file"));
    try {
      file_write(file_, kMagic.data(), kMagic.size());
      std::vector<unsigned char> header;
      header.reserve(12);
      append_u32_le(header, nx_);
      append_u32_le(header, ny_);
      append_u32_le(header, frames_);
      file_write(file_, header.data(), header.size());
      stream_.zalloc = Z_NULL;
      stream_.zfree = Z_NULL;
      stream_.opaque = Z_NULL;
      require(deflateInit(&stream_, level) == Z_OK, "zlib encoder initialization failed");
      zlib_active_ = true;
      previous_.assign(static_cast<std::size_t>(nx_) * ny_, 0);
    } catch (...) {
      if (zlib_active_) {
        deflateEnd(&stream_);
        zlib_active_ = false;
      }
      close_file();
      throw;
    }
  }

  ~Encoder() {
    if (zlib_active_) deflateEnd(&stream_);
    close_file();
  }

  void feed(const unsigned char* data, std::size_t size) {
    require(!finished_, "encoder is already finished");
    require(data || size == 0, "null encoder input");
    native_bytes_ += size;
    std::size_t consumed = 0;
    while (consumed < size) {
      const auto* newline = static_cast<const unsigned char*>(
          std::memchr(data + consumed, '\n', size - consumed));
      const std::size_t count = newline ?
          static_cast<std::size_t>(newline - (data + consumed)) + 1 : size - consumed;
      require(pending_.size() + count <= line_limit(), "unbounded native line");
      pending_.append(reinterpret_cast<const char*>(data + consumed), count);
      consumed += count;
      if (newline) {
        encode_line(pending_);
        pending_.clear();
      }
    }
  }

  void finish() {
    require(!finished_, "encoder is already finished");
    require(pending_.empty() && rows_ == static_cast<std::uint64_t>(frames_) * ny_,
            "truncated native fields");
    std::array<unsigned char, kChunk> output{};
    int status = Z_OK;
    while (status != Z_STREAM_END) {
      stream_.next_out = output.data();
      stream_.avail_out = static_cast<uInt>(output.size());
      status = deflate(&stream_, Z_FINISH);
      require(status == Z_OK || status == Z_STREAM_END, "zlib encoder finish failed");
      file_write(file_, output.data(), output.size() - stream_.avail_out);
    }
    const int end_status = deflateEnd(&stream_);
    zlib_active_ = false;
    require(end_status == Z_OK, "zlib encoder finalization failed");
    require(std::fflush(file_) == 0, "failed flushing codec file");
    close_file();
    finished_ = true;
  }

  std::uint64_t rows() const { return rows_; }
  std::uint64_t native_bytes() const { return native_bytes_; }

 private:
  std::size_t line_limit() const {
    return std::max(kChunk, static_cast<std::size_t>(nx_) * 32);
  }

  void close_file() {
    if (file_) {
      std::fclose(file_);
      file_ = nullptr;
    }
  }

  void write_record(const std::vector<unsigned char>& record) {
    stream_.next_in = const_cast<Bytef*>(record.data());
    stream_.avail_in = static_cast<uInt>(record.size());
    std::array<unsigned char, kChunk> output{};
    do {
      stream_.next_out = output.data();
      stream_.avail_out = static_cast<uInt>(output.size());
      const int status = deflate(&stream_, Z_NO_FLUSH);
      const std::size_t produced = output.size() - stream_.avail_out;
      require(status == Z_OK || (status == Z_BUF_ERROR && stream_.avail_in == 0),
              "zlib encoder failure");
      file_write(file_, output.data(), produced);
      if (status == Z_BUF_ERROR && produced == 0) break;
    } while (stream_.avail_in != 0 || stream_.avail_out == 0);
  }

  void encode_line(const std::string& line) {
    const std::string_view clean = stripped(line);
    if (clean.empty() || clean.front() == '%') {
      require(line.size() <= line_limit(), "literal exceeds bounded line");
      require(line.size() <= std::numeric_limits<std::uint32_t>::max(), "literal too long");
      std::vector<unsigned char> record;
      record.reserve(5 + line.size());
      record.push_back('L');
      append_u32_le(record, static_cast<std::uint32_t>(line.size()));
      record.insert(record.end(), line.begin(), line.end());
      write_record(record);
      return;
    }

    const auto split = tokens(clean);
    require(split.size() == nx_, "not a finite three-decimal canonical numeric row");
    std::vector<std::int32_t> values;
    values.reserve(nx_);
    std::string reconstructed;
    for (const auto token : split) {
      const std::int32_t value = parse_token(token);
      values.push_back(value);
      reconstructed += native_value(value);
    }
    reconstructed.push_back('\n');
    require(reconstructed == line, "native row is not exactly reconstructible canonical format");
    require(rows_ < static_cast<std::uint64_t>(frames_) * ny_, "extra native rows");

    const std::size_t offset = static_cast<std::size_t>(rows_ % ny_) * nx_;
    std::vector<unsigned char> record;
    record.reserve(1 + static_cast<std::size_t>(nx_) * 4);
    record.push_back('D');
    for (std::size_t index = 0; index < nx_; ++index) {
      const std::int64_t delta = static_cast<std::int64_t>(values[index]) - previous_[offset + index];
      require(delta >= std::numeric_limits<std::int32_t>::min() &&
                  delta <= std::numeric_limits<std::int32_t>::max(),
              "frame delta outside int32 range");
      append_i32_le(record, static_cast<std::int32_t>(delta));
      previous_[offset + index] = values[index];
    }
    write_record(record);
    ++rows_;
  }

  std::FILE* file_{};
  z_stream stream_{};
  bool zlib_active_{}, finished_{};
  std::uint32_t nx_{}, ny_{}, frames_{};
  std::vector<std::int32_t> previous_;
  std::string pending_;
  std::uint64_t rows_{}, native_bytes_{};
};

class Decoder {
 public:
  explicit Decoder(const char* path) {
    require(path && *path, "codec path is required");
    file_ = std::fopen(path, "rb");
    if (!file_) throw std::runtime_error(system_error("cannot open codec file"));
    try {
      std::array<unsigned char, 20> header{};
      require(std::fread(header.data(), 1, header.size(), file_) == header.size(),
              "truncated codec header");
      require(std::memcmp(header.data(), kMagic.data(), kMagic.size()) == 0,
              "codec magic mismatch");
      nx_ = read_u32_le(header.data() + 8);
      ny_ = read_u32_le(header.data() + 12);
      frames_ = read_u32_le(header.data() + 16);
      require(nx_ && ny_ && frames_ && static_cast<std::uint64_t>(nx_) * ny_ <= 400000,
              "invalid codec dimensions");
      stream_.zalloc = Z_NULL;
      stream_.zfree = Z_NULL;
      stream_.opaque = Z_NULL;
      require(inflateInit(&stream_) == Z_OK, "zlib decoder initialization failed");
      zlib_active_ = true;
      previous_.assign(static_cast<std::size_t>(nx_) * ny_, 0);
    } catch (...) {
      if (zlib_active_) {
        inflateEnd(&stream_);
        zlib_active_ = false;
      }
      close_file();
      throw;
    }
  }

  ~Decoder() {
    if (zlib_active_) inflateEnd(&stream_);
    close_file();
  }

  std::uint32_t nx() const { return nx_; }
  std::uint32_t ny() const { return ny_; }
  std::uint32_t frames() const { return frames_; }
  std::size_t line_capacity() const {
    return std::max(kChunk, static_cast<std::size_t>(nx_) * 32) + 1;
  }

  bool next(std::string& line) {
    require(!complete_, "decoder called after physical EOF validation");
    while (true) {
      if (decode_record(line)) return true;
      if (stream_end_) {
        compact();
        require(pending_.empty(), "truncated codec stream or frames");
        require(rows_ == static_cast<std::uint64_t>(ny_) * frames_,
                "truncated codec stream or frames");
        validate_physical_eof();
        complete_ = true;
        return false;
      }
      pump();
    }
  }

 private:
  std::size_t line_limit() const {
    return std::max(kChunk, static_cast<std::size_t>(nx_) * 32);
  }

  void close_file() {
    if (file_) {
      std::fclose(file_);
      file_ = nullptr;
    }
  }

  void compact() {
    if (pending_offset_ == 0) return;
    if (pending_offset_ == pending_.size()) {
      pending_.clear();
    } else {
      pending_.erase(pending_.begin(), pending_.begin() +
                                           static_cast<std::ptrdiff_t>(pending_offset_));
    }
    pending_offset_ = 0;
  }

  void pump() {
    compact();
    if (stream_.avail_in == 0 && !input_eof_) {
      const std::size_t count = std::fread(compressed_.data(), 1, compressed_.size(), file_);
      if (count == 0) {
        require(std::feof(file_), "failed reading codec file");
        input_eof_ = true;
      } else {
        stream_.next_in = compressed_.data();
        stream_.avail_in = static_cast<uInt>(count);
      }
    }

    stream_.next_out = inflated_.data();
    stream_.avail_out = static_cast<uInt>(inflated_.size());
    const uInt input_before = stream_.avail_in;
    const int status = inflate(&stream_, Z_NO_FLUSH);
    const std::size_t produced = inflated_.size() - stream_.avail_out;
    pending_.insert(pending_.end(), inflated_.begin(), inflated_.begin() +
                                                       static_cast<std::ptrdiff_t>(produced));
    if (status == Z_STREAM_END) {
      stream_end_ = true;
      require(stream_.avail_in == 0, "trailing compressed payload");
      return;
    }
    require(status == Z_OK || status == Z_BUF_ERROR, "corrupt codec stream");
    require(produced || stream_.avail_in < input_before || !input_eof_,
            "truncated codec stream or frames");
  }

  bool decode_record(std::string& line) {
    const std::size_t available = pending_.size() - pending_offset_;
    if (available == 0) return false;
    const unsigned char* record = pending_.data() + pending_offset_;
    if (record[0] == 'D') {
      const std::size_t length = 1 + static_cast<std::size_t>(nx_) * 4;
      if (available < length) return false;
      require(rows_ < static_cast<std::uint64_t>(ny_) * frames_, "extra codec rows");
      const std::size_t offset = static_cast<std::size_t>(rows_ % ny_) * nx_;
      line.clear();
      for (std::size_t index = 0; index < nx_; ++index) {
        const std::int32_t delta = read_i32_le(record + 1 + index * 4);
        const std::int64_t value = static_cast<std::int64_t>(previous_[offset + index]) + delta;
        require(value >= std::numeric_limits<std::int32_t>::min() &&
                    value <= std::numeric_limits<std::int32_t>::max(),
                "decoded value outside int32");
        previous_[offset + index] = static_cast<std::int32_t>(value);
        line += native_value(static_cast<std::int32_t>(value));
      }
      line.push_back('\n');
      pending_offset_ += length;
      ++rows_;
      return true;
    }
    if (record[0] == 'L') {
      if (available < 5) return false;
      const std::uint32_t count = read_u32_le(record + 1);
      require(count <= line_limit(), "literal exceeds bounded line");
      const std::size_t length = 5 + static_cast<std::size_t>(count);
      if (available < length) return false;
      line.assign(reinterpret_cast<const char*>(record + 5), count);
      const std::string_view clean = stripped(line);
      require(!line.empty() && line.back() == '\n' &&
                  (clean.empty() || clean.front() == '%'),
              "invalid literal line");
      pending_offset_ += length;
      return true;
    }
    throw std::invalid_argument("invalid codec record");
  }

  void validate_physical_eof() {
    unsigned char extra{};
    require(std::fread(&extra, 1, 1, file_) == 0 && std::feof(file_),
            "trailing compressed payload");
    const int end_status = inflateEnd(&stream_);
    zlib_active_ = false;
    require(end_status == Z_OK, "zlib decoder finalization failed");
    close_file();
  }

  std::FILE* file_{};
  z_stream stream_{};
  bool zlib_active_{}, input_eof_{}, stream_end_{}, complete_{};
  std::uint32_t nx_{}, ny_{}, frames_{};
  std::uint64_t rows_{};
  std::vector<std::int32_t> previous_;
  std::vector<unsigned char> pending_;
  std::size_t pending_offset_{};
  std::array<unsigned char, kChunk> compressed_{};
  std::array<unsigned char, kChunk> inflated_{};
};

void error_text(char* output, std::size_t capacity, const char* value) {
  if (!output || capacity == 0) return;
  const std::size_t count = std::min(capacity - 1, std::strlen(value));
  std::memcpy(output, value, count);
  output[count] = '\0';
}

template <typename Function>
int checked(char* error, std::size_t error_capacity, Function&& function) {
  try {
    function();
    if (error && error_capacity) error[0] = '\0';
    return 0;
  } catch (const std::exception& exception) {
    error_text(error, error_capacity, exception.what());
    return -1;
  } catch (...) {
    error_text(error, error_capacity, "unknown native codec error");
    return -1;
  }
}

}  // namespace

extern "C" {

void* eq3tmk_encoder_open(const char* path, std::uint32_t nx, std::uint32_t ny,
                          std::uint32_t frames, int level, char* error,
                          std::size_t error_capacity) {
  try {
    auto result = std::make_unique<Encoder>(path, nx, ny, frames, level);
    if (error && error_capacity) error[0] = '\0';
    return result.release();
  } catch (const std::exception& exception) {
    error_text(error, error_capacity, exception.what());
    return nullptr;
  }
}

int eq3tmk_encoder_feed(void* handle, const unsigned char* data, std::size_t size,
                        char* error, std::size_t error_capacity) {
  return checked(error, error_capacity, [&] {
    require(handle != nullptr, "null encoder handle");
    static_cast<Encoder*>(handle)->feed(data, size);
  });
}

int eq3tmk_encoder_finish(void* handle, std::uint64_t* rows,
                          std::uint64_t* native_bytes, char* error,
                          std::size_t error_capacity) {
  return checked(error, error_capacity, [&] {
    require(handle != nullptr && rows != nullptr && native_bytes != nullptr,
            "null encoder finish argument");
    auto* encoder = static_cast<Encoder*>(handle);
    encoder->finish();
    *rows = encoder->rows();
    *native_bytes = encoder->native_bytes();
  });
}

void eq3tmk_encoder_free(void* handle) { delete static_cast<Encoder*>(handle); }

void* eq3tmk_decoder_open(const char* path, std::uint32_t* nx, std::uint32_t* ny,
                          std::uint32_t* frames, std::size_t* line_capacity,
                          char* error, std::size_t error_capacity) {
  try {
    require(nx && ny && frames && line_capacity, "null decoder header argument");
    auto result = std::make_unique<Decoder>(path);
    *nx = result->nx();
    *ny = result->ny();
    *frames = result->frames();
    *line_capacity = result->line_capacity();
    if (error && error_capacity) error[0] = '\0';
    return result.release();
  } catch (const std::exception& exception) {
    error_text(error, error_capacity, exception.what());
    return nullptr;
  }
}

// Returns 1 for one line, 0 after validated physical EOF, and -1 on error.
int eq3tmk_decoder_next(void* handle, unsigned char* output, std::size_t capacity,
                        std::size_t* size, char* error, std::size_t error_capacity) {
  int outcome = -1;
  const int status = checked(error, error_capacity, [&] {
    require(handle && output && size, "null decoder next argument");
    std::string line;
    if (!static_cast<Decoder*>(handle)->next(line)) {
      *size = 0;
      outcome = 0;
      return;
    }
    require(line.size() <= capacity, "decoder output buffer is too small");
    std::memcpy(output, line.data(), line.size());
    *size = line.size();
    outcome = 1;
  });
  return status == 0 ? outcome : -1;
}

void eq3tmk_decoder_free(void* handle) { delete static_cast<Decoder*>(handle); }

const char* eq3tmk_codec_version() { return "EQ3TMK1-native-v1"; }

}  // extern "C"
