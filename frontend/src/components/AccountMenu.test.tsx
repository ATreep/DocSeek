import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import AccountMenu from "./AccountMenu";

afterEach(cleanup);

describe("AccountMenu", () => {
  it("keeps sign out behind an explicit menu action", async () => {
    const onSignOut = vi.fn();
    render(
      <AccountMenu
        onSignOut={onSignOut}
      />,
    );

    const avatar = screen.getByRole("button", { name: "Account menu" });
    expect(onSignOut).not.toHaveBeenCalled();
    await avatar.click();
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Settings" })).toBeNull();
    expect(onSignOut).not.toHaveBeenCalled();

    await screen.getByRole("menuitem", { name: "Sign out" }).click();
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });

  it("closes when the user clicks outside the account menu", async () => {
    render(<AccountMenu onSignOut={vi.fn()} />);

    await screen.getByRole("button", { name: "Account menu" }).click();
    expect(screen.getByRole("menu")).toBeTruthy();

    fireEvent.mouseDown(document.body);

    expect(screen.queryByRole("menu")).toBeNull();
  });
});
