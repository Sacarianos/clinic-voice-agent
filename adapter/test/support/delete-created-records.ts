import { afterAll } from "vitest";
import { deleteCreatedRecords } from "./ehr.ts";

afterAll(deleteCreatedRecords);
