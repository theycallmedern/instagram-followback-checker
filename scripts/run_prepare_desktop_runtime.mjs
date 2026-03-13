import { spawnSync } from "node:child_process";
import process from "node:process";

const commands =
  process.platform === "win32"
    ? [
        { cmd: "py", args: ["-3", "scripts/prepare_desktop_runtime.py"] },
        { cmd: "python", args: ["scripts/prepare_desktop_runtime.py"] },
      ]
    : [
        { cmd: "python3", args: ["scripts/prepare_desktop_runtime.py"] },
        { cmd: "python", args: ["scripts/prepare_desktop_runtime.py"] },
      ];

for (const { cmd, args } of commands) {
  const result = spawnSync(cmd, args, {
    stdio: "inherit",
    shell: false,
  });
  if (result.error && result.error.code === "ENOENT") {
    continue;
  }
  process.exit(result.status ?? 1);
}

console.error("No compatible Python launcher was found for desktop runtime preparation.");
process.exit(1);
