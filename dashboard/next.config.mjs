import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The dashboard lives in a subdirectory of the Python repo, and unrelated
  // lockfiles further up the tree make Next guess the wrong workspace root.
  outputFileTracingRoot: here,
};

export default nextConfig;
