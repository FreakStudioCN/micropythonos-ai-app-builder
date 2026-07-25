import { UPLOAD_CHUNK_SIZE } from "./config";

export type DeviceConnectionState = "disconnected" | "connecting" | "connected" | "error";

interface SerialReader {
  read(): Promise<{ value?: Uint8Array; done: boolean }>;
  cancel(): Promise<void>;
  releaseLock(): void;
}

interface SerialWriter {
  write(data: Uint8Array): Promise<void>;
  releaseLock(): void;
}

interface BrowserSerialPort {
  readable: { getReader(): SerialReader } | null;
  writable: { getWriter(): SerialWriter } | null;
  open(options: { baudRate: number; bufferSize?: number }): Promise<void>;
  close(): Promise<void>;
  getInfo?(): { usbVendorId?: number; usbProductId?: number };
}

interface BrowserSerial {
  requestPort(): Promise<BrowserSerialPort>;
  getPorts?(): Promise<BrowserSerialPort[]>;
}

interface OutputWaiter {
  needle: string;
  start: number;
  resolve: (output: string) => void;
  reject: (error: Error) => void;
  timer: number;
}

interface DeviceClientOptions {
  onData: (text: string) => void;
  onState: (state: DeviceConnectionState, message?: string) => void;
}

const serialFromNavigator = () =>
  (navigator as Navigator & { serial?: BrowserSerial }).serial;

const encodeBase64 = (text: string) => {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return window.btoa(binary);
};

const pythonString = (value: string) => JSON.stringify(value);

const decodeBase64 = (value: string) => {
  const binary = window.atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
};

export class DeviceDisconnectedError extends Error {
  constructor(message = "The ESP32 connection was lost") {
    super(message);
    this.name = "DeviceDisconnectedError";
  }
}

export class WebSerialDeviceClient {
  private port: BrowserSerialPort | null = null;
  private reader: SerialReader | null = null;
  private writer: SerialWriter | null = null;
  private reading = false;
  private decoder = new TextDecoder();
  private output = "";
  private waiters: OutputWaiter[] = [];
  private connectionGeneration = 0;
  private manualDisconnect = false;
  private selectedInfo: { usbVendorId?: number; usbProductId?: number } = {};
  private reconnecting: Promise<boolean> | null = null;
  private readonly options: DeviceClientOptions;

  constructor(options: DeviceClientOptions) {
    this.options = options;
  }

  static isSupported() {
    return Boolean(serialFromNavigator());
  }

  get connected() {
    return Boolean(this.reading && this.port && this.reader && this.writer);
  }

  async connect() {
    const serial = serialFromNavigator();
    if (!serial) {
      throw new Error("Web Serial is unavailable in this browser");
    }
    this.options.onState("connecting");
    const port = await serial.requestPort();
    this.manualDisconnect = false;
    return this.openPort(port);
  }

  private async openPort(port: BrowserSerialPort) {
    const info = port.getInfo?.() || {};
    // ESP32-S3 native USB (Espressif VID 0x303A) is not tied to a physical
    // 115200-baud UART. Asking Web Serial for a high baud rate removes the
    // conservative host-side throttle; UART bridge boards keep 115200.
    const baudRate = info.usbVendorId === 0x303a ? 921600 : 115200;
    try {
      await port.open({ baudRate, bufferSize: 65_536 });
    } catch (error) {
      try {
        await port.close();
      } catch {
        // A failed open may leave no closeable handle.
      }
      this.options.onState(
        "error",
        error instanceof Error ? error.message : String(error),
      );
      throw error;
    }
    if (!port.readable || !port.writable) {
      await port.close();
      throw new Error("The selected serial port cannot be read or written");
    }
    const generation = ++this.connectionGeneration;
    this.port = port;
    this.reader = port.readable.getReader();
    this.writer = port.writable.getWriter();
    this.decoder = new TextDecoder();
    this.reading = true;
    this.selectedInfo = info;
    const identity = info.usbVendorId
      ? `VID ${info.usbVendorId.toString(16).padStart(4, "0").toUpperCase()}`
      : "USB serial device";
    this.options.onState("connected", `${identity} · ${baudRate} baud`);
    void this.readLoop(generation);
    await this.writeRaw("\r\n");
    return { ...info, baudRate };
  }

  async disconnect() {
    this.manualDisconnect = true;
    ++this.connectionGeneration;
    this.reading = false;
    this.rejectWaiters(new DeviceDisconnectedError("Serial connection closed"));
    await this.releaseTransport();
    this.options.onState("disconnected");
  }

  private rejectWaiters(error: Error) {
    for (const waiter of this.waiters) {
      window.clearTimeout(waiter.timer);
      waiter.reject(error);
    }
    this.waiters = [];
  }

  private async releaseTransport() {
    try {
      await this.reader?.cancel();
    } catch {
      // The port may have been unplugged.
    }
    try {
      this.reader?.releaseLock();
    } catch {
      // Ignore an already released reader.
    }
    try {
      this.writer?.releaseLock();
    } catch {
      // Ignore an already released writer.
    }
    this.reader = null;
    this.writer = null;
    try {
      await this.port?.close();
    } catch {
      // The browser may already have closed the device.
    }
    this.port = null;
  }

  private async markTransportLost(error: unknown, generation: number) {
    if (generation !== this.connectionGeneration || this.manualDisconnect) return;
    ++this.connectionGeneration;
    this.reading = false;
    const rawMessage = error instanceof Error ? error.message : String(error || "");
    const message = /device has been lost|disconnected|networkerror/i.test(rawMessage)
      ? "ESP32 disconnected or restarted. Reconnecting…"
      : rawMessage || "The ESP32 connection was lost";
    this.rejectWaiters(new DeviceDisconnectedError(message));
    await this.releaseTransport();
    this.options.onState("error", message);
    void this.reconnect();
  }

  async reconnect(timeoutMs = 12_000) {
    if (this.connected) return true;
    if (this.reconnecting) return this.reconnecting;
    const serial = serialFromNavigator();
    if (!serial?.getPorts) return false;
    const getPorts = serial.getPorts.bind(serial);
    this.manualDisconnect = false;
    this.reconnecting = (async () => {
      const deadline = Date.now() + timeoutMs;
      this.options.onState("connecting", "Waiting for ESP32 to reconnect…");
      while (Date.now() < deadline) {
        const ports = await getPorts().catch(() => []);
        const candidates = ports.filter((candidate) => {
          const info = candidate.getInfo?.() || {};
          return (!this.selectedInfo.usbVendorId || info.usbVendorId === this.selectedInfo.usbVendorId)
            && (!this.selectedInfo.usbProductId || info.usbProductId === this.selectedInfo.usbProductId);
        });
        for (const candidate of candidates) {
          try {
            await this.openPort(candidate);
            return true;
          } catch {
            // A resetting USB device can be visible before it is ready to open.
          }
        }
        await new Promise((resolve) => window.setTimeout(resolve, 500));
      }
      this.options.onState(
        "error",
        "ESP32 did not reconnect. Unplug it, reconnect it, then click Connect ESP32.",
      );
      return false;
    })().finally(() => {
      this.reconnecting = null;
    });
    return this.reconnecting;
  }

  static isDisconnectError(error: unknown) {
    if (error instanceof DeviceDisconnectedError) return true;
    const message = error instanceof Error ? error.message : String(error);
    return /device has been lost|connection.*lost|disconnected|serial connection closed|networkerror/i.test(message);
  }

  async writeRaw(value: string | Uint8Array) {
    if (!this.writer) throw new Error("ESP32 is not connected");
    const data = typeof value === "string" ? new TextEncoder().encode(value) : value;
    const generation = this.connectionGeneration;
    try {
      await this.writer.write(data);
    } catch (error) {
      await this.markTransportLost(error, generation);
      throw new DeviceDisconnectedError(
        error instanceof Error ? error.message : "The ESP32 connection was lost",
      );
    }
  }

  async sendLine(command: string) {
    await this.writeRaw(`${command}\r\n`);
  }

  async interrupt() {
    await this.writeRaw(new Uint8Array([3]));
  }

  async execute(source: string, timeoutMs = 30_000) {
    const token = crypto.randomUUID().replace(/-/g, "");
    const okMarker = `__MPOS_OK_${token}__`;
    const errorMarker = `__MPOS_ERROR_${token}__`;
    const doneMarker = `__MPOS_DONE_${token}__`;
    const payload = encodeBase64(source);
    const wrapper = [
      "try:",
      ` exec(__import__('ubinascii').a2b_base64(${pythonString(payload)}).decode())`,
      ` print(${pythonString(okMarker)})`,
      "except Exception as e:",
      " import sys",
      " sys.print_exception(e)",
      ` print(${pythonString(errorMarker)} + repr(e))`,
      "finally:",
      ` print(${pythonString(doneMarker)})`,
    ].join("\n");
    const command = `exec(__import__('ubinascii').a2b_base64(${pythonString(encodeBase64(wrapper))}).decode())`;
    const start = this.output.length;
    const responsePromise = this.waitForOutput(doneMarker, start, timeoutMs);
    await this.sendLine(command);
    const output = await responsePromise;
    const errorIndex = output.indexOf(errorMarker);
    if (errorIndex >= 0) {
      const message = output.slice(errorIndex + errorMarker.length).split(/\r?\n/, 1)[0].trim();
      throw new Error(message || "Device command failed");
    }
    if (!output.includes(okMarker)) {
      throw new Error("Device command did not return a success marker");
    }
    return output;
  }

  async uploadBase64(
    remotePath: string,
    base64: string,
    onProgress: (percent: number) => void,
    deadline = Date.now() + 120_000,
  ) {
    const bytes = decodeBase64(base64);
    const token = crypto.randomUUID().replace(/-/g, "");
    const readyMarker = `__MPOS_UPLOAD_READY_${token}__`;
    const okMarker = `__MPOS_UPLOAD_OK_${token}__`;
    const errorMarker = `__MPOS_UPLOAD_ERROR_${token}__`;
    const doneMarker = `__MPOS_UPLOAD_DONE_${token}__`;

    await this.execute([
      "import os",
      "try:",
      " os.mkdir('/tmp')",
      "except OSError:",
      " pass",
    ].join("\n"));

    // Start one receiver on the device and then stream the MPK as raw bytes.
    // The previous implementation sent a Base64 command for every tiny chunk,
    // making every 384 bytes pay for a full REPL round trip and file stat.
    const receiver = [
      "import sys, os, micropython",
      `print(${pythonString(readyMarker)})`,
      "micropython.kbd_intr(-1)",
      `_f = open(${pythonString(remotePath)}, 'wb')`,
      `_remaining = ${bytes.length}`,
      "_written = 0",
      "try:",
      " while _remaining:",
      "  _part = sys.stdin.buffer.read(min(16384, _remaining))",
      "  if not _part:",
      "   raise OSError('serial upload ended before all bytes arrived')",
      "  _count = _f.write(_part)",
      "  _written += _count",
      "  _remaining -= len(_part)",
      " _f.close()",
      ` if _written != ${bytes.length}:`,
      "  raise OSError('serial upload wrote %d bytes' % _written)",
      ` print(${pythonString(okMarker)})`,
      "except Exception as e:",
      " try:",
      "  _f.close()",
      " except Exception:",
      "  pass",
      " sys.print_exception(e)",
      ` print(${pythonString(errorMarker)} + repr(e))`,
      "finally:",
      " micropython.kbd_intr(3)",
      ` print(${pythonString(doneMarker)})`,
    ].join("\n");
    const command = `exec(__import__('ubinascii').a2b_base64(${pythonString(encodeBase64(receiver))}).decode())`;
    const start = this.output.length;
    const readyPromise = this.waitForOutput(
      readyMarker,
      start,
      this.remainingInstallTime(deadline, 15_000),
    );
    await this.sendLine(command);
    await readyPromise;

    const secondsAtSlowSerialRate = Math.ceil(bytes.length / 2_000);
    const transferTimeout = this.remainingInstallTime(
      deadline,
      Math.max(30_000, (secondsAtSlowSerialRate + 20) * 1_000),
    );
    const donePromise = this.waitForOutput(doneMarker, start, transferTimeout);
    const writeSize = this.selectedInfo.usbVendorId === 0x303a ? 32_768 : 8_192;
    onProgress(0);
    for (let offset = 0; offset < bytes.length; offset += writeSize) {
      const end = Math.min(bytes.length, offset + writeSize);
      await this.writeRaw(bytes.subarray(offset, end));
      onProgress(Math.round((end / bytes.length) * 100));
    }
    const output = await donePromise;
    const errorIndex = output.indexOf(errorMarker);
    if (errorIndex >= 0) {
      const message = output.slice(errorIndex + errorMarker.length).split(/\r?\n/, 1)[0].trim();
      throw new Error(message || "Device rejected the MPK upload");
    }
    if (!output.includes(okMarker)) {
      throw new Error("Device did not confirm the MPK upload");
    }
    onProgress(100);
  }

  async installMpkBase64(
    packageName: string,
    base64: string,
    onProgress: (percent: number) => void,
  ) {
    const installStarted = performance.now();
    const deadline = Date.now() + 115_000;
    const bytes = decodeBase64(base64);
    this.logDiagnostic(
      `install start: ${packageName}, ${(bytes.length / 1024).toFixed(1)} KiB, budget 115s`,
    );
    const memoryMarker = `__MPOS_MEMORY_${crypto.randomUUID().replace(/-/g, "")}__`;
    const memoryOutput = await this.execute([
      "import gc",
      "gc.collect()",
      `print(${pythonString(memoryMarker)}, gc.mem_free())`,
    ].join("\n"), this.remainingInstallTime(deadline, 15_000));
    const freeMemory = Number(
      memoryOutput.match(new RegExp(`${memoryMarker}\\s+(\\d+)`))?.[1],
    );
    let canInstallFromRam = bytes.length <= 512 * 1024
      && Number.isFinite(freeMemory)
      && freeMemory > bytes.length * 2 + 96 * 1024;
    if (canInstallFromRam) {
      try {
        await this.execute([
          "import gc",
          `__mpos_probe = bytearray(${bytes.length})`,
          "del __mpos_probe",
          "gc.collect()",
        ].join("\n"), this.remainingInstallTime(deadline, 15_000));
      } catch {
        canInstallFromRam = false;
      }
    }

    // Low-memory boards use the official file installer. Native USB still
    // uploads the MPK in large raw chunks, so this remains much faster than
    // the old Base64-per-REPL-command path.
    if (!canInstallFromRam) {
      const tempPath = `/tmp/${packageName}.mpk`;
      this.logDiagnostic(`low-memory path: raw upload to ${tempPath}`);
      const uploadStarted = performance.now();
      await this.uploadBase64(
        tempPath,
        base64,
        (percent) => onProgress(Math.round(percent * 0.7)),
        deadline,
      );
      const uploadSeconds = Math.max(0.001, (performance.now() - uploadStarted) / 1000);
      this.logDiagnostic(
        `upload complete: ${uploadSeconds.toFixed(1)}s, ${(bytes.length / 1024 / uploadSeconds).toFixed(1)} KiB/s`,
      );
      onProgress(72);
      this.logDiagnostic("device install started");
      await this.execute([
        "from mpos import AppManager",
        `AppManager.install_mpk(${pythonString(tempPath)}, ${pythonString(`apps/${packageName}`)})`,
        `assert AppManager.is_installed_by_name(${pythonString(packageName)}), 'App install did not register'`,
      ].join("\n"), this.remainingInstallTime(deadline, 90_000));
      onProgress(100);
      this.logDiagnostic(
        `install complete: ${((performance.now() - installStarted) / 1000).toFixed(1)}s total`,
      );
      return;
    }

    this.logDiagnostic(`fast path: receive compressed MPK in RAM (${Math.round(freeMemory / 1024)} KiB free)`);
    const destination = `apps/${packageName}`;
    const token = crypto.randomUUID().replace(/-/g, "");
    const readyMarker = `__MPOS_INSTALL_READY_${token}__`;
    const okMarker = `__MPOS_INSTALL_OK_${token}__`;
    const errorMarker = `__MPOS_INSTALL_ERROR_${token}__`;
    const doneMarker = `__MPOS_INSTALL_DONE_${token}__`;

    // Receive the compressed MPK into RAM first and extract only after the
    // transfer finishes. Serial input is no longer throttled by decompression
    // and flash writes, which is both faster and less likely to drop USB.
    const receiver = [
      "import sys, os, micropython, shutil, gc",
      "from mpos import AppManager",
      "from mpos.content.streaming_unzip import StreamingUnzip",
      `_archive = bytearray(${bytes.length})`,
      `print(${pythonString(readyMarker)})`,
      "micropython.kbd_intr(-1)",
      "try:",
      " _offset = 0",
      ` while _offset < ${bytes.length}:`,
      `  _part = sys.stdin.buffer.read(min(32768, ${bytes.length} - _offset))`,
      "  if not _part:",
      "   raise OSError('serial install ended before all bytes arrived')",
      "  _archive[_offset:_offset + len(_part)] = _part",
      "  _offset += len(_part)",
      " try:",
      `  _st = os.stat(${pythonString(destination)})`,
      "  if _st[0] & 0x4000:",
      `   shutil.rmtree(${pythonString(destination)})`,
      "  else:",
      `   os.remove(${pythonString(destination)})`,
      " except OSError:",
      "  pass",
      `_extractor = StreamingUnzip(${pythonString(destination)}, expected_app_name=${pythonString(packageName)}, free_space_limit=lambda req: AppManager._check_free_space('.', req))`,
      " for _offset in range(0, len(_archive), 32768):",
      "  _extractor.feed(_archive[_offset:_offset + 32768])",
      " _extractor.finish()",
      " del _archive",
      " gc.collect()",
      " AppManager.refresh_apps()",
      ` if not AppManager.is_installed_by_name(${pythonString(packageName)}):`,
      "  raise OSError('App was extracted but is not registered')",
      ` print(${pythonString(okMarker)})`,
      "except Exception as e:",
      " try:",
      `  shutil.rmtree(${pythonString(destination)})`,
      " except Exception:",
      "  pass",
      " sys.print_exception(e)",
      ` print(${pythonString(errorMarker)} + repr(e))`,
      "finally:",
      " try:",
      "  del _archive",
      " except Exception:",
      "  pass",
      " gc.collect()",
      " micropython.kbd_intr(3)",
      ` print(${pythonString(doneMarker)})`,
    ].join("\n");
    const command = `exec(__import__('ubinascii').a2b_base64(${pythonString(encodeBase64(receiver))}).decode())`;
    const start = this.output.length;
    const readyPromise = this.waitForOutput(
      readyMarker,
      start,
      this.remainingInstallTime(deadline, 15_000),
    );
    await this.sendLine(command);
    await readyPromise;

    const secondsAtSlowSerialRate = Math.ceil(bytes.length / 2_000);
    const transferTimeout = this.remainingInstallTime(
      deadline,
      Math.max(30_000, (secondsAtSlowSerialRate + 25) * 1_000),
    );
    const donePromise = this.waitForOutput(doneMarker, start, transferTimeout);
    const writeSize = this.selectedInfo.usbVendorId === 0x303a ? 32_768 : 8_192;
    const transferStarted = performance.now();
    onProgress(0);
    for (let offset = 0; offset < bytes.length; offset += writeSize) {
      const end = Math.min(bytes.length, offset + writeSize);
      await this.writeRaw(bytes.subarray(offset, end));
      onProgress(Math.round((end / bytes.length) * 80));
    }
    onProgress(85);
    const transferSeconds = Math.max(0.001, (performance.now() - transferStarted) / 1000);
    this.logDiagnostic(
      `raw transfer complete: ${transferSeconds.toFixed(1)}s, ${(bytes.length / 1024 / transferSeconds).toFixed(1)} KiB/s; extracting`,
    );
    const output = await donePromise;
    const errorIndex = output.indexOf(errorMarker);
    if (errorIndex >= 0) {
      const message = output.slice(errorIndex + errorMarker.length).split(/\r?\n/, 1)[0].trim();
      throw new Error(message || "Device rejected the MPK install");
    }
    if (!output.includes(okMarker)) {
      throw new Error("Device did not confirm the MPK install");
    }
    onProgress(100);
    this.logDiagnostic(
      `install complete: ${((performance.now() - installStarted) / 1000).toFixed(1)}s total`,
    );
  }

  async uploadBase64WithReplChunks(
    remotePath: string,
    base64: string,
    onProgress: (percent: number) => void,
  ) {
    await this.execute([
      "import os",
      "try:",
      " os.mkdir('/tmp')",
      "except OSError:",
      " pass",
      `f = open(${pythonString(remotePath)}, 'wb')`,
      "f.close()",
    ].join("\n"));

    const chunkSize = UPLOAD_CHUNK_SIZE;
    for (let offset = 0; offset < base64.length; offset += chunkSize) {
      const chunk = base64.slice(offset, offset + chunkSize);
      const before = WebSerialDeviceClient.decodedLength(base64, offset);
      const after = WebSerialDeviceClient.decodedLength(
        base64,
        offset + chunk.length,
      );
      const snippet = [
        "import os, ubinascii",
        "try:",
        ` _n = os.stat(${pythonString(remotePath)})[6]`,
        "except OSError:",
        " _n = 0",
        `if _n == ${before}:`,
        ` _f = open(${pythonString(remotePath)}, 'ab')`,
        ` _f.write(ubinascii.a2b_base64(${pythonString(chunk)}))`,
        " _f.close()",
        `elif _n != ${after}:`,
        ` raise ValueError('upload out of sync at %d; expected ${before} or ${after}' % _n)`,
      ].join("\n");
      let lastError: unknown;
      for (let attempt = 1; attempt <= 3; attempt += 1) {
        try {
          await this.execute(snippet, 30_000);
          lastError = undefined;
          break;
        } catch (error) {
          lastError = error;
          if (attempt === 3) throw error;
        }
      }
      if (lastError) throw lastError;
      onProgress(Math.min(100, Math.round(((offset + chunk.length) / base64.length) * 100)));
    }
    const marker = `__MPOS_UPLOAD_SIZE_${crypto.randomUUID().replace(/-/g, "")}__`;
    const output = await this.execute([
      "import os",
      `print(${pythonString(marker)}, os.stat(${pythonString(remotePath)})[6])`,
    ].join("\n"), 15_000);
    const match = output.match(new RegExp(`${marker}\\s+(\\d+)`));
    const actual = Number(match?.[1]);
    const expected = WebSerialDeviceClient.decodedLength(
      base64,
      base64.length,
    );
    if (!Number.isFinite(actual) || actual !== expected) {
      throw new Error(
        `Upload verification failed: device has ${actual} bytes, expected ${expected}`,
      );
    }
  }

  private static decodedLength(base64: string, chars: number) {
    if (chars >= base64.length) {
      const padding = base64.endsWith("==")
        ? 2
        : base64.endsWith("=")
          ? 1
          : 0;
      return (base64.length / 4) * 3 - padding;
    }
    return (chars / 4) * 3;
  }

  private remainingInstallTime(deadline: number, cap: number) {
    const remaining = deadline - Date.now();
    if (remaining <= 1_000) {
      throw new Error("MPK install exceeded the 120 second time limit");
    }
    return Math.max(1_000, Math.min(cap, remaining));
  }

  private logDiagnostic(message: string) {
    this.options.onData(`\n[MPK] ${message}\n`);
  }

  private waitForOutput(needle: string, start: number, timeoutMs: number) {
    return new Promise<string>((resolve, reject) => {
      const existing = this.output.indexOf(needle, start);
      if (existing >= 0) {
        resolve(this.output.slice(start, existing + needle.length));
        return;
      }
      const waiter: OutputWaiter = {
        needle,
        start,
        resolve,
        reject,
        timer: window.setTimeout(() => {
          this.waiters = this.waiters.filter((item) => item !== waiter);
          reject(new Error(`Timed out waiting for the ESP32 after ${Math.round(timeoutMs / 1000)} seconds`));
        }, timeoutMs),
      };
      this.waiters.push(waiter);
    });
  }

  private appendOutput(text: string) {
    this.output += text;
    this.options.onData(text);
    const completed: OutputWaiter[] = [];
    for (const waiter of this.waiters) {
      const end = this.output.indexOf(waiter.needle, waiter.start);
      if (end < 0) continue;
      window.clearTimeout(waiter.timer);
      waiter.resolve(this.output.slice(waiter.start, end + waiter.needle.length));
      completed.push(waiter);
    }
    if (completed.length) {
      this.waiters = this.waiters.filter((waiter) => !completed.includes(waiter));
    }
  }

  private async readLoop(generation: number) {
    let terminalError: unknown;
    try {
      while (this.reading && this.reader && generation === this.connectionGeneration) {
        const { value, done } = await this.reader.read();
        if (done) break;
        if (value?.length) this.appendOutput(this.decoder.decode(value, { stream: true }));
      }
      const tail = this.decoder.decode();
      if (tail) this.appendOutput(tail);
    } catch (error) {
      terminalError = error;
    } finally {
      if (this.reading && generation === this.connectionGeneration) {
        await this.markTransportLost(
          terminalError || new DeviceDisconnectedError("The ESP32 serial stream closed"),
          generation,
        );
      }
    }
  }
}
