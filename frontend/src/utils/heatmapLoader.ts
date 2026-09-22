import JSZip from "jszip"
import { parse } from "npyjs"

export type HeatmapData = {
  x: number[]
  y: number[]
  z: number[][]
}

function toNumberArray(arr: { readonly length: number; [k: number]: unknown }): number[] {
  const out: number[] = new Array(arr.length)
  for (let i = 0; i < arr.length; i++) {
    out[i] = Number(arr[i])
  }
  return out
}

/**
 * Reshape a flat 2D array using the shape returned by npyjs. If shape is
 * missing (older npyjs versions) fall back to len(y) × len(x).
 */
function reshape(flat: number[], rows: number, cols: number): number[][] {
  const out: number[][] = new Array(rows)
  for (let i = 0; i < rows; i++) {
    out[i] = flat.slice(i * cols, (i + 1) * cols)
  }
  return out
}

export async function openHeatmapFile(path: string): Promise<HeatmapData> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`Could not open heatmap file: ${response.status}`)

  const buffer = await response.arrayBuffer()
  const zip = await JSZip.loadAsync(buffer)

  const xFile = zip.file("x.npy")
  const yFile = zip.file("y.npy")
  const zFile = zip.file("z.npy")
  if (!xFile || !yFile || !zFile) {
    throw new Error("NPZ file is missing x.npy, y.npy, or z.npy")
  }

  const [xParsed, yParsed, zParsed] = await Promise.all([
    xFile.async("arraybuffer").then(parse),
    yFile.async("arraybuffer").then(parse),
    zFile.async("arraybuffer").then(parse),
  ])

  const unsafe = (x: unknown) => x as { readonly length: number; [k: number]: unknown }
  const x = toNumberArray(unsafe(xParsed.data))
  const y = toNumberArray(unsafe(yParsed.data))
  const zFlat = toNumberArray(unsafe(zParsed.data))

  let z: number[][]
  const shape = zParsed.shape as number[] | undefined
  if (Array.isArray(shape) && shape.length === 2) {
    const [rows, cols] = shape
    z = reshape(zFlat, rows, cols)
  } else {
    z = reshape(zFlat, y.length, x.length)
  }

  return { x, y, z }
}
