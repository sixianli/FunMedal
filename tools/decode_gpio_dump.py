import collections
import re
import sys


def load_signal_names(header_path):
    inputs, outputs = collections.defaultdict(list), collections.defaultdict(list)
    for match in re.finditer(r"#define\s+(\w+?)_(IN|OUT)_IDX\s+(\d+)", open(header_path).read()):
        (inputs if match.group(2) == "IN" else outputs)[int(match.group(3))].append(match.group(1))
    for match in re.finditer(r"#define\s+(\w+_OUT\d+)_IDX\s+(\d+)", open(header_path).read()):
        outputs[int(match.group(2))].append(match.group(1))
    return inputs, outputs


def section(text, name):
    body = text.split("== " + name, 1)[1].split("==", 1)[0]
    return [int(word, 16) for word in re.findall(r"(?<=\s)([0-9a-f]{8})(?=\s|$)", body)]


def main(header_path, dump_path):
    inputs, outputs = load_signal_names(header_path)
    text = open(dump_path).read()
    out_sel, in_sel, io_mux = section(text, "OUT_SEL"), section(text, "IN_SEL"), section(text, "IO_MUX")
    enable, out_level, in_level = section(text, "ENABLE")
    print("GPIO  output-signal          OE  IE  PU  MCU_SEL  IN  OUT")
    for gpio in range(22):
        signal = out_sel[gpio] & 0xFF
        name = "GPIO(simple)" if signal == 128 else "/".join(outputs.get(signal, ["?"]))
        mux = io_mux[gpio]
        print(f"{gpio:>4}  {name:<22} {(enable >> gpio) & 1:>2}  {(mux >> 9) & 1:>2}  {(mux >> 8) & 1:>2}"
              f"  {(mux >> 12) & 7:>7}  {(in_level >> gpio) & 1:>2}  {(out_level >> gpio) & 1:>3}")
    print("\ninput signals routed through the GPIO matrix:")
    for signal, value in enumerate(in_sel):
        if value & 0x40:
            pin = value & 0x1F
            source = {0x1E: "const-high", 0x1F: "const-low"}.get(pin, f"GPIO{pin}")
            print(f"  {signal:>3} {'/'.join(inputs.get(signal, ['?'])):<20} <- {source}")
    onetime = section(text, "SARADC_ONETIME a")[0]
    print(f"\nlast one-shot ADC sample: channel {(onetime >> 25) & 0xF}, atten {(onetime >> 23) & 0x3}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
