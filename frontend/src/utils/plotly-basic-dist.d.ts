// `plotly.js-basic-dist` ships the same JS API surface as `plotly.js`
// (minus the heavy 3D / mapbox / finance / gl2d distributions), so its
// types are equivalent to `@types/plotly.js`.
declare module "plotly.js-basic-dist" {
  export * from "plotly.js"
  export { default } from "plotly.js"
}
