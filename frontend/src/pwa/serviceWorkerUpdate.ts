/** A quiz in progress should keep the bundle it started on. */
export function updateReloadBlocked(pathname: string): boolean {
  return pathname === "/quizzes" || pathname.startsWith("/quizzes/");
}

export function reloadApp(): void {
  window.location.reload();
}
