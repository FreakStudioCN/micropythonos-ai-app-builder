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

export class WebSerialDeviceClient {
  private port: BrowserSerialPort | null = null;
  private reader: SerialReader | null = null;
  private writer: SerialWriter | null = null;
  private reading = false;
  private decoder = new TextDecoder();
  private output = "";
  private waiters: OutputWaiter[] = [];
  private readonly options: DeviceClientOptions;

  constructor(options: DeviceClientOptions) {
    this.options = options;
  }

  static isSupported() {
    return Boolean(serialFromNavigator());
  }

  get connected() {
    return Boolean(this.port && this.reader && this.writer);
  }

  async connect() {
    const serial = serialFromNavigator();
    if (!serial) {
      throw new Error("Web Serial is unavailable in this browser");
    }
    this.options.onState("connecting");
    const port = await serial.requestPort();
    try {
      await port.open({ baudRate: 115200, bufferSize: 4096 });
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
    this.port = port;
    this.reader = port.readable.getReader();
    this.writer = port.writable.getWriter();
    this.reading = true;
    const info = port.getInfo?.() || {};
    const identity = info.usbVendorId
      ? `VID ${info.usbVendorId.toString(16).padStart(4, "0").toUpperCase()}`
      : "USB serial device";
    this.options.onState("connected", identity);
    void this.readLoop();
    await this.writeRaw("\r\n");
    return info;
  }

  async disconnect() {
    this.reading = false;
    for (const waiter of this.waiters) {
      window.clearTimeout(waiter.timer);
      waiter.reject(new Error("Serial connection closed"));
    }
    this.waiters = [];
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
    this.options.onState("disconnected");
  }

  async writeRaw(value: string | Uint8Array) {
    if (!this.writer) throw new Error("ESP32 is not connected");
    const data = typeof value === "string" ? new TextEncoder().encode(value) : value;
    await this.writer.write(data);
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

  private async readLoop() {
    try {
      while (this.reading && this.reader) {
        const { value, done } = await this.reader.read();
        if (done) break;
        if (value?.length) this.appendOutput(this.decoder.decode(value, { stream: true }));
      }
      const tail = this.decoder.decode();
      if (tail) this.appendOutput(tail);
    } catch (error) {
      if (this.reading) {
        const message = error instanceof Error ? error.message : String(error);
        this.options.onState("error", message);
      }
    }
  }
}
