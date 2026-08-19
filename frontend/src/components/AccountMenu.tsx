import { LogOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useDocSeekTranslation } from "../i18n";
import "./ui-hardening.css";

export default function AccountMenu({
  onSignOut,
}: {
  onSignOut: () => void | Promise<void>;
}) {
  const { t } = useDocSeekTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  async function signOut() {
    setOpen(false);
    await onSignOut();
  }

  return (
    <div className="account-menu" ref={rootRef}>
      <button
        type="button"
        className="avatar"
        aria-label={t("Account menu")}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((current) => !current)}
      >
        A
      </button>
      {open && (
        <div className="account-menu-panel" role="menu">
          <button type="button" role="menuitem" onClick={() => void signOut()}>
            <LogOut size={15} /> {t("Sign out")}
          </button>
        </div>
      )}
    </div>
  );
}
