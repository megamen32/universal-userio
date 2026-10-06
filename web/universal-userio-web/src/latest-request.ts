export class LatestRequest {
  private sequence = 0

  begin(): number {
    this.sequence += 1
    return this.sequence
  }

  isCurrent(request: number): boolean {
    return request === this.sequence
  }
}
