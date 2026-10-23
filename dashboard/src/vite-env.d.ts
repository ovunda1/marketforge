/// <reference types="vite/client" />

declare module "virtual:facts" {
  const facts: import("../facts.config").Facts;
  export default facts;
}
