export class RoboGuideApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | null = null,
  ) {
    super(message);
    this.name = "RoboGuideApiError";
  }
}
