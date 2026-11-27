// Shared by the live API client (lib/api.ts) and the replay client (lib/replay.ts), which must not import each other.

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
