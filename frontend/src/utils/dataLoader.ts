import JSZip from "jszip"
import { parse } from "npyjs"

export type Data = {
  x: number[]
  y: number[]
}

/**
 * Coerce a npyjs TypedArray to a plain JS number[]. The TypedArray runtime
 * always exposes a numeric `length` and numeric indices, even though TS's
 * `ArrayBufferView` doesn't declare them — so we read it as `any`.
 */
function toNumberArray(arr: { readonly length: number; [k: number]: unknown }): number[] {
  const out: number[] = new Array(arr.length)
  for (let i = 0; i < arr.length; i++) {
    out[i] = Number(arr[i])
  }
  return out
}

export async function openDataFile(path: string): Promise<Data> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`Could not open data file: ${response.status}`)

  const buffer = await response.arrayBuffer()
  const zip = await JSZip.loadAsync(buffer)

  const xFile = zip.file("x.npy")
  const yFile = zip.file("y.npy")
  if (!xFile || !yFile) {
    throw new Error("NPZ file is missing x.npy or y.npy")
  }

  const [xParsed, yParsed] = await Promise.all([
    xFile.async("arraybuffer").then(parse),
    yFile.async("arraybuffer").then(parse),
  ])

  return {
    x: toNumberArray(xParsed.data as unknown as { readonly length: number; [k: number]: unknown }),
    y: toNumberArray(yParsed.data as unknown as { readonly length: number; [k: number]: unknown }),
  }
}
