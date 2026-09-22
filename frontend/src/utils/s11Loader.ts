import JSZip from "jszip"
import { parse } from "npyjs"

export type S11Data = {
  frequency: number[]
  s11Real: number[]
  s11Imag: number[]
}

function toNumberArray(arr: { readonly length: number; [k: number]: unknown }): number[] {
  const out: number[] = new Array(arr.length)
  for (let i = 0; i < arr.length; i++) {
    out[i] = Number(arr[i])
  }
  return out
}

export async function openS11File(path: string): Promise<S11Data> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`Could not open S11 file: ${response.status}`)

  const buffer = await response.arrayBuffer()
  const zip = await JSZip.loadAsync(buffer)

  const freqFile = zip.file("frequency.npy")
  const realFile = zip.file("real.npy")
  const imagFile = zip.file("imag.npy")
  if (!freqFile || !realFile || !imagFile) {
    throw new Error("S11 NPZ file is missing frequency.npy, real.npy, or imag.npy")
  }

  const [freqParsed, realParsed, imagParsed] = await Promise.all([
    freqFile.async("arraybuffer").then(parse),
    realFile.async("arraybuffer").then(parse),
    imagFile.async("arraybuffer").then(parse),
  ])

  const unsafe = (x: unknown) => x as { readonly length: number; [k: number]: unknown }
  return {
    frequency: toNumberArray(unsafe(freqParsed.data)),
    s11Real: toNumberArray(unsafe(realParsed.data)),
    s11Imag: toNumberArray(unsafe(imagParsed.data)),
  }
}
