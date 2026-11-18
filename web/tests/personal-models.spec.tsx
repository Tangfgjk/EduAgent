import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, test, expect } from "vitest";
import PersonalModels from "@/features/settings/sections/models/PersonalModelsSection";
const request = vi.hoisted(() => vi.fn());
vi.mock("@/shared/api/client", () => ({requestJson: request}));
vi.mock("@/components/access/CapabilityAccessContext", () => ({useCapabilityAccess: () => ({refresh: vi.fn()})}));
test("student saves private model and clears secret input", async () => {
  request.mockResolvedValueOnce({configured:false}).mockResolvedValueOnce({configured:true});
  render(<PersonalModels />);
  await waitFor(() => expect(screen.getByRole("button", {name:"保存配置"})).toBeEnabled());
  fireEvent.change(screen.getByLabelText("模型 ID"), {target:{value:"deepseek-chat"}});
  fireEvent.change(screen.getByLabelText("API Key"), {target:{value:"fixture-key"}});
  fireEvent.click(screen.getByRole("button", {name:"保存配置"}));
  await screen.findByRole("status");
  expect(JSON.parse(request.mock.calls[1][1].body)).toMatchObject({provider:"deepseek", model:"deepseek-chat", api_key:"fixture-key"});
  expect(screen.getByLabelText("API Key")).toHaveValue("");
});
