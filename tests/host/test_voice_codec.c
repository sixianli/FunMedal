#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "voice_codec.h"

#define PI 3.14159265358979323846
#define PACKETS 50
#define TOTAL (PACKETS * VOICE_SAMPLES_PER_PACKET)

static int failures;

static void fail(const char *what, const char *detail)
{
    printf("FAIL %s: %s\n", what, detail);
    failures++;
}

static const int16_t STEPS[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,    28,
    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,   118,
    130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,   494,
    544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,
    2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,
    9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
};
static const int8_t INDEX_STEP[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

static int16_t input[TOTAL];
static int16_t output[TOTAL];
static uint8_t packets[PACKETS][VOICE_AUDIO_PACKET];

static int16_t reference_decode(int *predictor, int *index, uint8_t code)
{
    int step = STEPS[*index];
    int diff = step >> 3;
    if (code & 4) {
        diff += step;
    }
    if (code & 2) {
        diff += step >> 1;
    }
    if (code & 1) {
        diff += step >> 2;
    }
    *predictor += (code & 8) ? -diff : diff;
    if (*predictor > INT16_MAX) {
        *predictor = INT16_MAX;
    }
    if (*predictor < INT16_MIN) {
        *predictor = INT16_MIN;
    }
    *index += INDEX_STEP[code];
    if (*index < 0) {
        *index = 0;
    }
    if (*index > 88) {
        *index = 88;
    }
    return (int16_t)*predictor;
}

static void decode_packet(const uint8_t *packet, int16_t *out)
{
    int predictor = (int16_t)(packet[4] | (packet[5] << 8));
    int index = packet[6];
    for (int i = 0; i < VOICE_SAMPLES_PER_PACKET / 2; i++) {
        uint8_t byte = packet[VOICE_AUDIO_HEADER + i];
        out[2 * i] = reference_decode(&predictor, &index, byte & 0x0f);
        out[2 * i + 1] = reference_decode(&predictor, &index, byte >> 4);
    }
}

static void encode_all(uint8_t clip)
{
    adpcm_state_t state = {0, 0};
    for (int p = 0; p < PACKETS; p++) {
        size_t n = voice_audio_packet(clip, (uint16_t)p, &state, input + p * VOICE_SAMPLES_PER_PACKET, packets[p]);
        if (n != VOICE_AUDIO_PACKET) {
            fail("audio packet size", "every audio packet is 167 bytes");
        }
    }
}

static void decode_all(void)
{
    for (int p = 0; p < PACKETS; p++) {
        decode_packet(packets[p], output + p * VOICE_SAMPLES_PER_PACKET);
    }
}

static double rms(const int16_t *a, const int16_t *b, int count)
{
    double sum = 0;
    for (int i = 0; i < count; i++) {
        double d = (double)a[i] - (b != NULL ? (double)b[i] : 0.0);
        sum += d * d;
    }
    return sqrt(sum / count);
}

static void silence(void)
{
    memset(input, 0, sizeof(input));
    encode_all(7);
    decode_all();
    int worst = 0;
    for (int i = 0; i < TOTAL; i++) {
        if (abs(output[i]) > worst) {
            worst = abs(output[i]);
        }
    }
    if (worst > 8) {
        fail("silence", "decodes to within 8 of zero");
    }
}

static void sine(void)
{
    for (int i = 0; i < TOTAL; i++) {
        input[i] = (int16_t)lround(8000.0 * sin(2 * PI * 1000.0 * i / VOICE_SAMPLE_RATE));
    }
    encode_all(1);
    decode_all();
    int settled = VOICE_SAMPLES_PER_PACKET;
    double error = rms(input + settled, output + settled, TOTAL - settled);
    double signal = rms(input + settled, NULL, TOTAL - settled);
    printf("sine error rms %.1f, signal rms %.1f\n", error, signal);
    if (error >= 0.1 * signal) {
        fail("1 kHz sine", "error stays under a tenth of the signal");
    }
}

static void full_scale_square(void)
{
    for (int i = 0; i < TOTAL; i++) {
        input[i] = ((i / 8) % 2) ? INT16_MAX : INT16_MIN;
    }
    encode_all(2);
    decode_all();
    int crossings = 0;
    for (int i = 1; i < TOTAL; i++) {
        crossings += (output[i - 1] < 0) != (output[i] < 0);
    }
    if (crossings <= TOTAL / 16) {
        fail("full scale square", "keeps its sign changes without overflow");
    }
}

static void packets_stand_alone(void)
{
    for (int i = 0; i < TOTAL; i++) {
        input[i] = (int16_t)((i * 7919) % 20000 - 10000);
    }
    encode_all(3);
    static int16_t whole[TOTAL];
    int predictor = 0;
    int index = 0;
    for (int p = 0; p < PACKETS; p++) {
        int header_predictor = (int16_t)(packets[p][4] | (packets[p][5] << 8));
        if (header_predictor != predictor || packets[p][6] != index) {
            fail("packet header", "holds the encoder state before the packet");
        }
        for (int i = 0; i < VOICE_SAMPLES_PER_PACKET / 2; i++) {
            uint8_t byte = packets[p][VOICE_AUDIO_HEADER + i];
            whole[p * VOICE_SAMPLES_PER_PACKET + 2 * i] = reference_decode(&predictor, &index, byte & 0x0f);
            whole[p * VOICE_SAMPLES_PER_PACKET + 2 * i + 1] = reference_decode(&predictor, &index, byte >> 4);
        }
    }
    decode_all();
    if (memcmp(whole, output, sizeof(whole)) != 0) {
        fail("packets stand alone", "each packet decodes on its own to the same samples as the whole stream");
    }
}

static void header_fields(void)
{
    memset(input, 0, sizeof(input));
    adpcm_state_t state = {0, 0};
    uint8_t packet[VOICE_AUDIO_PACKET];
    voice_audio_packet(200, 0x1234, &state, input, packet);
    if (packet[0] != VOICE_PACKET_AUDIO || packet[1] != 200 || packet[2] != 0x34 || packet[3] != 0x12) {
        fail("audio header", "type, clip, then sequence low byte first");
    }
    uint8_t start[VOICE_START_PACKET];
    if (voice_start_packet(9, start) != VOICE_START_PACKET || start[0] != VOICE_PACKET_START || start[1] != 9) {
        fail("start packet", "type then clip");
    }
    uint8_t end[VOICE_END_PACKET];
    if (voice_end_packet(9, 0x0102, VOICE_END_LIMIT, end) != VOICE_END_PACKET || end[0] != VOICE_PACKET_END ||
        end[1] != 9 || end[2] != 0x02 || end[3] != 0x01 || end[4] != VOICE_END_LIMIT) {
        fail("end packet", "type, clip, packet count low byte first, reason");
    }
}

static void nibble_order(void)
{
    int16_t pair[VOICE_SAMPLES_PER_PACKET] = {2000, -2000};
    adpcm_state_t state = {0, 0};
    uint8_t packet[VOICE_AUDIO_PACKET];
    voice_audio_packet(0, 0, &state, pair, packet);
    uint8_t byte = packet[VOICE_AUDIO_HEADER];
    if ((byte & 0x08) != 0 || (byte & 0x80) == 0) {
        fail("nibble order", "first sample in the low four bits, second in the high four");
    }
}

static void expect_result(const char *what, const char *data, bool ok, uint8_t clip, voice_result_t result)
{
    uint8_t got_clip = 0x5a;
    voice_result_t got = VOICE_RESULT_TIMEOUT;
    bool parsed = voice_result_parse(data, strlen(data), &got_clip, &got);
    if (parsed != ok || (ok && (got_clip != clip || got != result))) {
        fail("result value", what);
    }
}

static void results(void)
{
    expect_result("queued", "7\tqueued", true, 7, VOICE_RESULT_QUEUED);
    expect_result("submitted", "7\tsubmitted", true, 7, VOICE_RESULT_SUBMITTED);
    expect_result("empty", "0\tempty", true, 0, VOICE_RESULT_EMPTY);
    expect_result("no session", "255\tno_session", true, 255, VOICE_RESULT_NO_SESSION);
    expect_result("denied", "12\tdenied", true, 12, VOICE_RESULT_DENIED);
    expect_result("cleared", "3\tcleared", true, 3, VOICE_RESULT_CLEARED);
    expect_result("stopped", "4\tstopped", true, 4, VOICE_RESULT_STOPPED);
    expect_result("refused", "5\trefused", true, 5, VOICE_RESULT_REFUSED);
    expect_result("the old word for queued", "7\tsent", false, 0, VOICE_RESULT_QUEUED);
    expect_result("clip above 255", "256\tqueued", false, 0, VOICE_RESULT_QUEUED);
    expect_result("no clip", "\tqueued", false, 0, VOICE_RESULT_QUEUED);
    expect_result("clip with a sign", "+1\tqueued", false, 0, VOICE_RESULT_QUEUED);
    expect_result("no tab", "1 queued", false, 0, VOICE_RESULT_QUEUED);
    expect_result("unknown word", "1\tok", false, 0, VOICE_RESULT_QUEUED);
    expect_result("card-only word offline", "1\toffline", false, 0, VOICE_RESULT_QUEUED);
    expect_result("card-only word timeout", "1\ttimeout", false, 0, VOICE_RESULT_QUEUED);
    expect_result("trailing bytes", "1\tqueued\n", false, 0, VOICE_RESULT_QUEUED);
    expect_result("empty value", "", false, 0, VOICE_RESULT_QUEUED);
    const char *names[] = {"queued", "submitted", "empty", "no_session", "denied",
                           "cleared", "stopped", "refused", "offline", "timeout"};
    for (int i = 0; i < 10; i++) {
        if (strcmp(voice_result_name((voice_result_t)i), names[i]) != 0) {
            fail("result name", names[i]);
        }
    }
}

static bool write_chunk(voice_image_t *image, uint8_t clip, uint16_t offset, const uint8_t *data, size_t len)
{
    uint8_t chunk[VOICE_IMAGE_HEADER + 512];
    chunk[0] = clip;
    chunk[1] = (uint8_t)(offset & 0xff);
    chunk[2] = (uint8_t)(offset >> 8);
    memcpy(chunk + VOICE_IMAGE_HEADER, data, len);
    return voice_image_write(image, chunk, VOICE_IMAGE_HEADER + len);
}

static bool write_whole(voice_image_t *image, uint8_t clip, const uint8_t *pixels, size_t chunk)
{
    for (size_t offset = 0; offset < VOICE_IMAGE_BYTES; offset += chunk) {
        size_t len = VOICE_IMAGE_BYTES - offset < chunk ? VOICE_IMAGE_BYTES - offset : chunk;
        if (!write_chunk(image, clip, (uint16_t)offset, pixels + offset, len)) {
            return false;
        }
    }
    return true;
}

static void images(void)
{
    static uint8_t pixels[VOICE_IMAGE_BYTES];
    for (size_t i = 0; i < sizeof(pixels); i++) {
        pixels[i] = (uint8_t)(i * 37 + 11);
    }
    static voice_image_t image;
    voice_image_reset(&image);
    if (voice_image_complete(&image, 0)) {
        fail("image", "a reset image is not complete");
    }
    if (!write_whole(&image, 4, pixels, 500) || !voice_image_complete(&image, 4) ||
        memcmp(image.pixels, pixels, sizeof(pixels)) != 0) {
        fail("image", "chunks written in order make the whole image");
    }
    if (voice_image_complete(&image, 5)) {
        fail("image", "complete only for its own clip");
    }
    voice_image_reset(&image);
    write_chunk(&image, 4, 0, pixels, 500);
    if (write_chunk(&image, 4, 1000, pixels + 1000, 500)) {
        fail("image", "a chunk that skips ahead is refused");
    }
    if (write_chunk(&image, 4, 500, pixels + 500, 500) || voice_image_complete(&image, 4)) {
        fail("image", "after a refused chunk the clip must start again from offset 0");
    }
    voice_image_reset(&image);
    write_chunk(&image, 4, 0, pixels, 500);
    if (write_chunk(&image, 5, 500, pixels + 500, 500)) {
        fail("image", "a chunk for another clip is refused");
    }
    voice_image_reset(&image);
    write_whole(&image, 4, pixels, 500);
    if (!write_chunk(&image, 6, 0, pixels, 100) || voice_image_complete(&image, 4) || voice_image_complete(&image, 6)) {
        fail("image", "offset 0 starts a new image and drops the old one");
    }
    voice_image_reset(&image);
    write_chunk(&image, 4, 0, pixels, 500);
    for (uint16_t offset = 500; offset < 2500; offset += 500) {
        write_chunk(&image, 4, offset, pixels + offset, 500);
    }
    if (write_chunk(&image, 4, 2500, pixels + 2500, 301)) {
        fail("image", "a chunk past the end is refused");
    }
    uint8_t header_only[VOICE_IMAGE_HEADER] = {4, 0, 0};
    if (voice_image_write(&image, header_only, sizeof(header_only))) {
        fail("image", "a chunk without pixel bytes is refused");
    }
    static uint8_t one_pixel[VOICE_IMAGE_BYTES];
    one_pixel[0] = 0x80;
    one_pixel[VOICE_IMAGE_STRIDE + 1] = 0x01;
    voice_image_reset(&image);
    write_whole(&image, 1, one_pixel, 509);
    if (!voice_image_pixel(image.pixels, 0, 0) || voice_image_pixel(image.pixels, 1, 0) ||
        !voice_image_pixel(image.pixels, 15, 1) || voice_image_pixel(image.pixels, 14, 1) ||
        voice_image_pixel(image.pixels, 0, 1)) {
        fail("image", "rows are 28 bytes, the high bit of each byte is the leftmost pixel, 1 is ink");
    }
}

int main(void)
{
    results();
    images();
    silence();
    sine();
    full_scale_square();
    packets_stand_alone();
    header_fields();
    nibble_order();
    if (failures == 0) {
        printf("ALL PASSED\n");
    }
    return failures == 0 ? 0 : 1;
}
