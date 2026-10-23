/* Minimal QR Code encoder for the RoomCall card: byte mode, error correction level M.
   Follows the structure of Nayuki's public-domain reference implementation. */
const ECC = [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28,
  28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28];
const BLOCKS = [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25,
  26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49];

const bit = (x, i) => ((x >>> i) & 1) !== 0;

function rawModules(ver) {
  let result = (16 * ver + 128) * ver + 64;
  if (ver >= 2) {
    const align = Math.floor(ver / 7) + 2;
    result -= (25 * align - 10) * align - 55;
    if (ver >= 7) result -= 36;
  }
  return result;
}
const dataCodewords = (ver) => Math.floor(rawModules(ver) / 8) - ECC[ver] * BLOCKS[ver];

function multiply(x, y) {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z;
}
function divisor(degree) {
  const result = new Array(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < result.length; j++) {
      result[j] = multiply(result[j], root);
      if (j + 1 < result.length) result[j] ^= result[j + 1];
    }
    root = multiply(root, 0x02);
  }
  return result;
}
function remainder(data, div) {
  const result = div.map(() => 0);
  for (const b of data) {
    const factor = b ^ result.shift();
    result.push(0);
    div.forEach((coef, i) => { result[i] ^= multiply(coef, factor); });
  }
  return result;
}

function encodeData(text) {
  const bytes = Array.from(new TextEncoder().encode(text));
  for (let ver = 1; ver <= 40; ver++) {
    const countBits = ver < 10 ? 8 : 16;
    const capacity = dataCodewords(ver) * 8;
    if (4 + countBits + bytes.length * 8 > capacity) continue;
    const bits = [];
    const append = (value, length) => { for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1); };
    append(0x4, 4);
    append(bytes.length, countBits);
    bytes.forEach((b) => append(b, 8));
    append(0, Math.min(4, capacity - bits.length));
    append(0, (8 - (bits.length % 8)) % 8);
    for (let pad = 0xec; bits.length < capacity; pad ^= 0xec ^ 0x11) append(pad, 8);
    const codewords = [];
    for (let i = 0; i < bits.length; i += 8) codewords.push(parseInt(bits.slice(i, i + 8).join(""), 2));
    return { ver, codewords };
  }
  throw new Error("Text too long for a QR code");
}

function interleave(ver, data) {
  const blocks = BLOCKS[ver], eccLen = ECC[ver], raw = Math.floor(rawModules(ver) / 8);
  const shortBlocks = blocks - (raw % blocks), shortLen = Math.floor(raw / blocks);
  const div = divisor(eccLen);
  const all = [];
  for (let i = 0, k = 0; i < blocks; i++) {
    const dat = data.slice(k, k + shortLen - eccLen + (i < shortBlocks ? 0 : 1));
    k += dat.length;
    const ecc = remainder(dat, div);
    if (i < shortBlocks) dat.push(0);
    all.push(dat.concat(ecc));
  }
  const result = [];
  for (let i = 0; i < all[0].length; i++) {
    all.forEach((block, j) => { if (i !== shortLen - eccLen || j >= shortBlocks) result.push(block[i]); });
  }
  return result;
}

function alignmentPositions(ver, size) {
  if (ver === 1) return [];
  const count = Math.floor(ver / 7) + 2;
  const step = ver === 32 ? 26 : Math.ceil((ver * 4 + 4) / (count * 2 - 2)) * 2;
  const result = [6];
  for (let pos = size - 7; result.length < count; pos -= step) result.splice(1, 0, pos);
  return result;
}

const MASKS = [
  (x, y) => (x + y) % 2 === 0, (x, y) => y % 2 === 0, (x) => x % 3 === 0, (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0, (x, y) => (x * y) % 2 + (x * y) % 3 === 0,
  (x, y) => ((x * y) % 2 + (x * y) % 3) % 2 === 0, (x, y) => (((x + y) % 2) + (x * y) % 3) % 2 === 0,
];

function build(ver, codewords, mask) {
  const size = ver * 4 + 17;
  const modules = Array.from({ length: size }, () => new Array(size).fill(false));
  const fixed = Array.from({ length: size }, () => new Array(size).fill(false));
  const set = (x, y, dark) => { modules[y][x] = dark; fixed[y][x] = true; };
  for (let i = 0; i < size; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
      const dist = Math.max(Math.abs(dx), Math.abs(dy)), x = cx + dx, y = cy + dy;
      if (x >= 0 && x < size && y >= 0 && y < size) set(x, y, dist !== 2 && dist !== 4);
    }
  }
  const align = alignmentPositions(ver, size), last = align.length - 1;
  align.forEach((ax, i) => align.forEach((ay, j) => {
    if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) return;
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
      set(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }));
  // Format bits: level M is 0b00.
  const data = mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  const format = ((data << 10) | rem) ^ 0x5412;
  for (let i = 0; i <= 5; i++) set(8, i, bit(format, i));
  set(8, 7, bit(format, 6)); set(8, 8, bit(format, 7)); set(7, 8, bit(format, 8));
  for (let i = 9; i < 15; i++) set(14 - i, 8, bit(format, i));
  for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(format, i));
  for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(format, i));
  set(8, size - 8, true);
  if (ver >= 7) {
    let v = ver;
    for (let i = 0; i < 12; i++) v = (v << 1) ^ ((v >>> 11) * 0x1f25);
    const bits = (ver << 12) | v;
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3), b = Math.floor(i / 3);
      set(a, b, bit(bits, i)); set(b, a, bit(bits, i));
    }
  }
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) for (let j = 0; j < 2; j++) {
      const x = right - j, upward = ((right + 1) & 2) === 0, y = upward ? size - 1 - vert : vert;
      if (!fixed[y][x] && i < codewords.length * 8) {
        modules[y][x] = bit(codewords[i >>> 3], 7 - (i & 7));
        i++;
      }
    }
  }
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    if (!fixed[y][x] && MASKS[mask](x, y)) modules[y][x] = !modules[y][x];
  }
  return modules;
}

/** Penalty rules 1, 2 and 4 of the standard: long runs, 2×2 blocks and dark/light balance. */
function penalty(modules) {
  const size = modules.length;
  let score = 0, dark = 0;
  for (const lines of [modules, modules[0].map((_, x) => modules.map((row) => row[x]))]) {
    for (const line of lines) {
      let run = 1;
      for (let i = 1; i <= size; i++) {
        if (i < size && line[i] === line[i - 1]) { run++; continue; }
        if (run >= 5) score += run - 2;
        run = 1;
      }
    }
  }
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    if (modules[y][x]) dark++;
    if (x < size - 1 && y < size - 1) {
      const c = modules[y][x];
      if (c === modules[y][x + 1] && c === modules[y + 1][x] && c === modules[y + 1][x + 1]) score += 3;
    }
  }
  return score + Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size)) * 10;
}

export function qrModules(text) {
  const { ver, codewords } = encodeData(text);
  const data = interleave(ver, codewords);
  let best = null, bestScore = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    const modules = build(ver, data, mask);
    const score = penalty(modules);
    if (score < bestScore) { best = modules; bestScore = score; }
  }
  return best;
}

/** An SVG QR code with the standard four-module quiet zone, dark on light for any theme. */
export function qrSvg(text, label) {
  const modules = qrModules(text), size = modules.length, view = size + 8;
  let path = "";
  modules.forEach((row, y) => row.forEach((dark, x) => { if (dark) path += `M${x + 4} ${y + 4}h1v1h-1z`; }));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${view} ${view}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  svg.setAttribute("shape-rendering", "crispEdges");
  svg.innerHTML = `<rect width="${view}" height="${view}" fill="#fff"/><path d="${path}" fill="#000"/>`;
  return svg;
}
