// `plotly.js-cartesian-dist` ships the same JS API surface as `plotly.js`
// (minus the heavy 3D / mapbox / finance / gl2d distributions), so its
// types are equivalent to `@types/plotly.js`. This unblocks the TS
// declaration lookup for `import Plotly from "plotly.js-cartesian-dist"`.
declare module "plotly.js-cartesian-dist" {
  export * from "plotly.js"
  export { default } from "plotly.js"
}
