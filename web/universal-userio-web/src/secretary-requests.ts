import { LatestRequest } from "./latest-request.ts"

// A poll cannot cancel an action or overwrite its result; changing chats cancels both.
export class SecretaryRequests {
  private reads = new LatestRequest()
  private actions = new LatestRequest()
  private acting = false

  reset(): void {
    this.reads.begin()
    this.actions.begin()
    this.acting = false
  }

  beginRead(): number | null {
    return this.acting ? null : this.reads.begin()
  }

  isCurrentRead(request: number): boolean {
    return !this.acting && this.reads.isCurrent(request)
  }

  beginAction(): number | null {
    if (this.acting) return null
    this.acting = true
    this.reads.begin()
    return this.actions.begin()
  }

  isCurrentAction(request: number): boolean {
    return this.actions.isCurrent(request)
  }

  finishAction(request: number): boolean {
    if (!this.isCurrentAction(request)) return false
    this.reads.begin()
    this.acting = false
    return true
  }
}
