import { chromium } from "@playwright/test";
import { existsSync } from "node:fs";

const url = process.env.TB_E2E_URL || "http://127.0.0.1:18777";
const systemChrome = "/usr/bin/google-chrome";
const executablePath = process.env.TB_E2E_CHROME || (existsSync(systemChrome) ? systemChrome : chromium.executablePath());
const output = process.env.TB_E2E_SCREENSHOT || "/tmp/ticketbunker-standalone.png";
const browser = await chromium.launch({ executablePath, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 940 } });
const errors = [];
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});
page.on("pageerror", (error) => errors.push(error.message));

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  if (await page.getByRole("heading", { name: "Your local identity" }).isVisible().catch(() => false)) {
    await page.getByLabel("Name").fill("TicketBunker QA");
    await page.getByLabel("Email").fill("qa@example.test");
    await page.getByLabel("Branch name").fill("qa");
    await page.getByRole("button", { name: "Continue" }).click();
  }
  await page.waitForTimeout(300);
  if (await page.getByRole("button", { name: "Use without agent" }).isVisible().catch(() => false)) {
    await page.getByRole("button", { name: "Use without agent" }).click();
  }
  await page.getByRole("button", { name: "Bunkers", exact: true }).click();
  await page.getByRole("heading", { name: "Bunkers", exact: true }).waitFor();
  const importButton = page.getByRole("button", { name: "Import copy" });
  if (await importButton.isVisible().catch(() => false)) {
    await importButton.click();
    await page.getByRole("button", { name: "Imported" }).waitFor();
  }
  await page.screenshot({ path: output.replace(/\.png$/, "-bunkers.png"), fullPage: true });
  await page.locator(".bunker-overview > button").first().click();
  await page.getByRole("heading", { name: "Tickets", exact: true }).waitFor();
  await page.getByRole("button", { name: "Configure" }).click();
  await page.getByRole("heading", { name: "Configuration" }).waitFor();
  await page.getByRole("button", { name: "Integrations" }).click();
  await page.getByLabel("Integration").selectOption("github");
  await page.locator('.mapping-row input[value="title"]').first().waitFor();
  await page.getByRole("button", { name: "Publish mappings" }).click();
  await page.getByText("Integration mappings published").waitFor();
  await page.screenshot({ path: output.replace(/\.png$/, "-configuration.png"), fullPage: true });
  await page.getByRole("button", { name: "Back" }).click();
  await page.locator(".ticket-row").first().click();
  await page.locator(".ticket-heading h1").waitFor();
  await page.screenshot({ path: output, fullPage: true });
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await page.getByRole("heading", { name: "Agent work" }).waitFor();
  await page.screenshot({ path: output.replace(/\.png$/, "-agent.png"), fullPage: true });
  for (const width of [900, 640]) {
    await page.setViewportSize({ width, height: 820 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 1) throw new Error(`standalone layout overflows by ${overflow}px at ${width}px`);
  }
  const editorPage = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  await editorPage.goto(`${url}/source?path=README.md&line=1`, { waitUntil: "domcontentloaded" });
  await editorPage.locator(".direct-editor > textarea").waitFor();
  await editorPage.screenshot({ path: output.replace(/\.png$/, "-editor.png") });
  await editorPage.close();
  if (errors.length) throw new Error(`browser errors:\n${errors.join("\n")}`);
} finally {
  await browser.close();
}
