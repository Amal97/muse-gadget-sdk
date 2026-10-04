// SPDX-License-Identifier: Apache-2.0
import Foundation
import EventKit
import AppKit

struct EventQuery: Decodable {
    let ids: [String]
    let start: Double
    let end: Double
}

struct Exchange: Decodable {
    let command: String
    let query: EventQuery?
}

var responseURL: URL?

func output(_ object: Any) throws {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    if let url = responseURL {
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        return
    }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

var command = CommandLine.arguments.dropFirst().first ?? "status"
if command == "request-access" {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    app.finishLaunching()
    app.activate(ignoringOtherApps: true)
}
do {
    var exchange: Exchange?
    if command == "exchange" {
        guard CommandLine.arguments.count == 3 else {
            throw NSError(domain: "MuseCalendar", code: 5, userInfo: [
                NSLocalizedDescriptionKey: "Expected a private calendar request file."])
        }
        let request = URL(fileURLWithPath: CommandLine.arguments[2]).resolvingSymlinksInPath()
        let directory = request.deletingLastPathComponent()
        let attributes = try FileManager.default.attributesOfItem(atPath: directory.path)
        guard request.lastPathComponent == "request.json",
              directory.lastPathComponent.hasPrefix("muse-calendar-"),
              (attributes[.posixPermissions] as? NSNumber)?.intValue == 0o700,
              (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid() else {
            throw NSError(domain: "MuseCalendar", code: 6, userInfo: [
                NSLocalizedDescriptionKey: "Calendar exchange must belong to the current user."])
        }
        responseURL = directory.appendingPathComponent("response.json")
        let decoded = try JSONDecoder().decode(Exchange.self, from: Data(contentsOf: request))
        exchange = decoded
        command = decoded.command
        guard ["status", "calendars", "events"].contains(command) else {
            throw NSError(domain: "MuseCalendar", code: 7, userInfo: [
                NSLocalizedDescriptionKey: "Invalid read-only calendar command."])
        }
    }
    let store = EKEventStore()
    let authorized = EKEventStore.authorizationStatus(for: .event) == .fullAccess
    if command == "status" {
        try output(["authorized": authorized,
                    "authorization": EKEventStore.authorizationStatus(for: .event).rawValue,
                    "identity": Bundle.main.bundleIdentifier ?? "unbundled"] as [String: Any])
    } else if command == "request-access" {
        let finished = DispatchSemaphore(value: 0)
        store.requestFullAccessToEvents { allowed, error in
            do {
                try output(["authorized": allowed,
                            "error": error?.localizedDescription ?? ""])
            } catch {
                FileHandle.standardError.write(Data("Could not report calendar authorization.\n".utf8))
            }
            finished.signal()
        }
        while finished.wait(timeout: .now()) != .success {
            RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05))
        }
    } else {
        guard authorized else {
            throw NSError(domain: "MuseCalendar", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "Grant Muse Calendar Reader full calendar access first."])
        }
        let calendars = store.calendars(for: .event)
        if command == "calendars" {
            try output(["calendars": calendars.map {
                ["id": $0.calendarIdentifier, "name": $0.title]
            }])
        } else if command == "events" {
            let query: EventQuery
            if let supplied = exchange?.query {
                query = supplied
            } else {
                query = try JSONDecoder().decode(
                    EventQuery.self, from: FileHandle.standardInput.readDataToEndOfFile())
            }
            guard query.start.isFinite, query.end.isFinite, query.end > query.start,
                  query.end - query.start <= 3 * 86400,
                  Set(query.ids).count == query.ids.count else {
                throw NSError(domain: "MuseCalendar", code: 2, userInfo: [
                    NSLocalizedDescriptionKey: "Invalid calendar query."])
            }
            let selected = calendars.filter { query.ids.contains($0.calendarIdentifier) }
            guard selected.count == query.ids.count else {
                throw NSError(domain: "MuseCalendar", code: 3, userInfo: [
                    NSLocalizedDescriptionKey: "An enabled calendar is unavailable; update calendar settings."])
            }
            let events = selected.isEmpty ? [] : store.events(matching: store.predicateForEvents(
                withStart: Date(timeIntervalSince1970: query.start),
                end: Date(timeIntervalSince1970: query.end), calendars: selected))
            try output(["events": events.sorted { $0.startDate < $1.startDate }.map {
                ["title": $0.title ?? "(Untitled event)", "start": $0.startDate.timeIntervalSince1970,
                 "end": $0.endDate.timeIntervalSince1970, "all_day": $0.isAllDay,
                 "calendar": $0.calendar.title] as [String: Any]
            }])
        } else {
            throw NSError(domain: "MuseCalendar", code: 4, userInfo: [
                NSLocalizedDescriptionKey: "Unknown calendar-reader command."])
        }
    }
} catch {
    if responseURL != nil {
        do {
            try output(["error": error.localizedDescription])
        } catch {
            FileHandle.standardError.write(Data("Could not report calendar reader failure.\n".utf8))
        }
    }
    FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
    exit(1)
}
