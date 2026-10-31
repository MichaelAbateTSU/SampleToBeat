import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStudio } from "../stores/studio";
import { makeArrangement } from "../test/fixtures";
import PatternEditor from "./PatternEditor";

vi.mock("../audio/engine", () => ({ engine: { audition: vi.fn() } }));

const renderEditor = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } })}>
      <PatternEditor samples={{}} />
    </QueryClientProvider>,
  );

describe("PatternEditor", () => {
  beforeEach(() => useStudio.getState().load("p1", makeArrangement()));

  it("asks for a selection when nothing is selected", () => {
    renderEditor();
    expect(screen.getByText(/Select a clip/)).toBeInTheDocument();
  });

  it("toggles drum steps into the shared pattern, undoably", () => {
    useStudio.getState().select({ clipId: "c2", trackId: "kick" });
    renderEditor();
    expect(screen.getByText(/Shared by 2 clips/)).toBeInTheDocument();
    const cell = screen.getByLabelText("kick step 5");
    expect(cell).toHaveAttribute("data-on", "false");
    fireEvent.click(cell);
    expect(screen.getByLabelText("kick step 5")).toHaveAttribute("data-on", "true");
    expect(useStudio.getState().arr!.patterns.pd.events.some((e) => e.tick === 480)).toBe(true);
    useStudio.getState().undo();
    expect(useStudio.getState().arr!.patterns.pd.events.some((e) => e.tick === 480)).toBe(false);
  });

  it("explains that chops need samples", () => {
    useStudio.getState().select({ clipId: "c1", trackId: "sample" });
    renderEditor();
    expect(screen.getByText(/No samples in this project/)).toBeInTheDocument();
  });
});
