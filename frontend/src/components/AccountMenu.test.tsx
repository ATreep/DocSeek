import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import AccountMenu from "./AccountMenu";

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
});
