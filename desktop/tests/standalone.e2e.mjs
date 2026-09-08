import { chromium } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";

const { version } = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));

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
  const identity = page.getByRole("heading", { name: "Your local identity" });
  const withoutAgent = page.getByRole("button", { name: "Use without agent" });
  const bunkersNavigation = page.getByRole("button", { name: "All Bunkers", exact: true });
  await identity.or(withoutAgent).or(bunkersNavigation).first().waitFor({ state: "visible" });
  if (await identity.isVisible()) {
    await page.getByLabel("Name", { exact: true }).fill("TicketBunker QA");
    await page.getByLabel("Email", { exact: true }).fill("qa@example.test");
    await page.getByLabel("Branch name", { exact: true }).fill("qa");
    await page.getByRole("button", { name: "Continue" }).click();
    await withoutAgent.waitFor({ state: "visible" });
  }
  if (await withoutAgent.isVisible()) await withoutAgent.click();
  await bunkersNavigation.click();
  await page.getByRole("heading", { name: "Bunkers", exact: true }).waitFor();
  await page.locator(".profile-control").click();
  await page.getByRole("button", { name: "System", exact: true }).click();
  await page.getByText(version, { exact: true }).waitFor();
  await page.getByRole("button", { name: "Check now", exact: true }).waitFor();
  await page.screenshot({ path: output.replace(/\.png$/, "-system.png"), fullPage: true });
  await page.getByRole("button", { name: "Close settings" }).click();
  const importButton = page.getByRole("button", { name: "Import copy" });
  if (await importButton.isVisible().catch(() => false)) {
    await importButton.click();
    await page.getByRole("button", { name: "Imported" }).waitFor();
  }
  await page.screenshot({ path: output.replace(/\.png$/, "-bunkers.png"), fullPage: true });
  await page.locator(".bunker-overview > button").first().click();
  await page.getByRole("heading", { name: "Projects", exact: true }).waitFor();
  if (await page.getByLabel("Search tickets").isVisible().catch(() => false)) {
    throw new Error("a Bunker page must list Projects, not a ticket catalog");
  }
  await page.screenshot({ path: output.replace(/\.png$/, "-projects.png"), fullPage: true });
  await page.locator(".project-row").first().click();
  await page.locator(".nav-project.selected").waitFor();
  await page.getByLabel("Search tickets").waitFor();
  await page.getByRole("button", { name: "Configure" }).click();
  await page.getByRole("heading", { name: "Configuration" }).waitFor();
  await page.getByRole("heading", { name: "Boards", exact: true }).waitFor();
  await page.getByRole("button", { name: "Repositories" }).click();
  await page.getByRole("table", { name: "Local repositories" }).waitFor();
  await page.screenshot({ path: output.replace(/\.png$/, "-repositories.png"), fullPage: true });
  await page.getByRole("button", { name: "Boards", exact: true }).click();
  await page.getByText("Adjust field mapping").click();
  await page.locator('.mapping-row input[value="title"]').first().waitFor();
  await page.getByRole("button", { name: "Save mappings" }).click();
  await page.getByText("Integration mappings saved").waitFor();
  await page.screenshot({ path: output.replace(/\.png$/, "-configuration.png"), fullPage: true });
  await page.getByRole("button", { name: "Back" }).click();
  await page.locator(".ticket-row").first().click();
  await page.locator(".ticket-heading h1").waitFor();
  await page.screenshot({ path: output, fullPage: true });
  await page.getByRole("heading", { name: "Agent work" }).waitFor();
  await page.screenshot({ path: output.replace(/\.png$/, "-agent.png"), fullPage: true });
  // Opt-in only: the caller must provide a disposable, clean checkout with
  // a reachable scratch origin. Ordinary viewer smoke tests never create work.
  if (process.env.TB_E2E_CREATE_BRANCH) {
    const name = process.env.TB_E2E_CREATE_BRANCH;
    await page.getByRole("button", { name: "Create branch", exact: true }).click();
    await page.getByLabel("Short description").fill(name);
    await page.getByRole("button", { name: "Review branch", exact: true }).click();
    await page.locator(".confirmation").getByRole("button", { name: "Create branch", exact: true }).click();
    await page.locator(".branch-item").filter({ hasText: name }).waitFor();
    await page.screenshot({ path: output.replace(/\.png$/, "-branch-created.png"), fullPage: true });
  }
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
} catch (error) {
  await page.screenshot({ path: output.replace(/\.png$/, "-failure.png"), fullPage: true });
  console.error(`Visible page at failure:\n${await page.locator("body").innerText()}`);
  throw error;
} finally {
  await browser.close();
}
