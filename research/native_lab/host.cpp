// Independently written synthetic host. No game attachment, injection or external service.
#define NOMINMAX
#include <windows.h>
#include <dbghelp.h>
#include <cstddef>
#include <cstdint>
#include <chrono>
#include <cstring>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef LAB_VARIANT
#define LAB_VARIANT 0
#endif

struct SampleRecord { std::uint32_t schema; std::uint64_t sequence; std::int32_t count; };
extern "C" __declspec(dllexport) __declspec(noinline) int FixtureAdd(int a, int b) {
    return a + b + LAB_VARIANT;
}

struct Event { int epoch; int sequence; int count; int cells; };
static const Event fixtures[] = {
    {1, 1, 0, 2}, {1, 1, 0, 2}, {1, 0, 9, -1}, {0, 8, 2, -1},
    {1, 2, -2, -1}, {1, 3, -1, -1}, {2, 1, 5, -1}, {1, 3, 1, -1}
};

std::string encode(const Event& event) {
    return std::to_string(event.epoch) + ',' + std::to_string(event.sequence) + ','
        + std::to_string(event.count) + ',' + std::to_string(event.cells);
}
Event decode(const std::string& wire) {
    std::istringstream input(wire); Event event{}; char a, b, c;
    if (!(input >> event.epoch >> a >> event.sequence >> b >> event.count >> c >> event.cells)
        || a != ',' || b != ',' || c != ',' || input.peek() != EOF) {
        throw std::invalid_argument("invalid synthetic wire");
    }
    return event;
}
void emit(const Event& event) {
    std::cout << "{\"schema_version\":1,\"producer_id\":\"synthetic\",\"epoch\":" << event.epoch
        << ",\"sequence\":" << event.sequence << ",\"facts\":{";
    bool comma = false;
    if (event.count != -1) {
        std::cout << "\"count\":";
        if (event.count == -2) std::cout << "null"; else std::cout << event.count;
        comma = true;
    }
    if (event.cells != -1) {
        if (comma) std::cout << ',';
        std::cout << "\"cells\":" << event.cells;
    }
    std::cout << "}}\n";
}

int symbol_pair(const wchar_t* image, const wchar_t* pdb) {
    SYMSRV_INDEX_INFOW a{}, b{}; a.sizeofstruct = sizeof(a); b.sizeofstruct = sizeof(b);
    if (!SymSrvGetFileIndexInfoW(image, &a, 0) || !SymSrvGetFileIndexInfoW(pdb, &b, 0)) {
        std::cout << "{\"status\":\"index_error\",\"win32_error\":" << GetLastError() << "}\n";
        return 2;
    }
    bool match = std::memcmp(&a.guid, &b.guid, sizeof(GUID)) == 0 && a.age == b.age;
    std::cout << "{\"synthetic\":true,\"matched\":" << (match ? "true" : "false")
        << ",\"image_age\":" << a.age << ",\"pdb_age\":" << b.age << "}\n";
    return match ? 0 : 3;
}

__declspec(noinline) void FixtureStackLeaf() {
    void* frames[16]{}; const auto count = CaptureStackBackTrace(0, 16, frames, nullptr);
    std::vector<std::string> names;
    for (USHORT i = 0; i < count; ++i) {
        alignas(SYMBOL_INFO) char storage[sizeof(SYMBOL_INFO) + MAX_SYM_NAME]{};
        auto* symbol = reinterpret_cast<SYMBOL_INFO*>(storage);
        symbol->SizeOfStruct = sizeof(SYMBOL_INFO); symbol->MaxNameLen = MAX_SYM_NAME;
        DWORD64 displacement = 0;
        if (SymFromAddr(GetCurrentProcess(), reinterpret_cast<DWORD64>(frames[i]), &displacement, symbol)) {
            std::string name(symbol->Name, symbol->NameLen);
            if (name.find("FixtureStack") != std::string::npos) names.push_back(name);
        }
    }
    std::cout << "{\"synthetic\":true,\"capture_phase\":\"before_controlled_throw\",\"fixture_frames\":[";
    for (size_t i = 0; i < names.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << '"' << names[i] << '"';
    }
    std::cout << "]}\n";
    throw std::runtime_error("controlled synthetic exception");
}
__declspec(noinline) void FixtureStackMiddle() { FixtureStackLeaf(); }

int wmain(int argc, wchar_t** argv) {
    try {
        if (argc == 2 && std::wstring(argv[1]) == L"identity") {
            auto base = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
            auto address = reinterpret_cast<std::uintptr_t>(&FixtureAdd);
            std::cout << "{\"synthetic\":true,\"variant\":" << LAB_VARIANT
                << ",\"pid\":" << GetCurrentProcessId() << ",\"loaded_base\":" << base
                << ",\"function_va\":" << address << ",\"rva\":" << address - base
                << ",\"pointer_size\":" << sizeof(void*) << ",\"record_size\":" << sizeof(SampleRecord)
                << ",\"sequence_offset\":" << offsetof(SampleRecord, sequence)
                << ",\"count_offset\":" << offsetof(SampleRecord, count)
                << ",\"call_result\":" << FixtureAdd(2, 3) << "}\n";
            return 0;
        }
        if (argc == 4 && std::wstring(argv[1]) == L"symbols") return symbol_pair(argv[2], argv[3]);
        if (argc == 3 && std::wstring(argv[1]) == L"stack") {
            SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS | SYMOPT_FAIL_CRITICAL_ERRORS
                | SYMOPT_NO_PROMPTS | SYMOPT_IGNORE_NT_SYMPATH);
            if (!SymInitializeW(GetCurrentProcess(), argv[2], TRUE)) return 2;
            try { FixtureStackMiddle(); } catch (const std::runtime_error&) { /* expected */ }
            SymCleanup(GetCurrentProcess()); return 0;
        }
        if (argc == 4 && std::wstring(argv[1]) == L"events") {
            auto mode = std::wstring(argv[2]);
            if (mode != L"encoded" && mode != L"typed") return 2;
            std::wstring amount(argv[3]); size_t used = 0; int repeat = std::stoi(amount, &used);
            if (used != amount.size() || repeat < 1 || repeat > 1000) return 2;
            auto started = std::chrono::steady_clock::now(); int decoded = 0;
            for (int i = 0; i < repeat; ++i) for (const auto& event : fixtures) {
                if (mode == L"encoded") { emit(decode(encode(event))); ++decoded; } else emit(event);
            }
            std::cerr << "{\"events\":" << repeat * 8 << ",\"wire_decodes\":" << decoded
                << ",\"emit_nanoseconds\":" << std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now() - started).count() << "}\n";
            return 0;
        }
        if (argc == 4 && std::wstring(argv[1]) == L"server") {
            auto mode = std::wstring(argv[2]);
            if (mode != L"encoded" && mode != L"typed") return 2;
            std::wstring text(argv[3]); size_t used = 0; int epoch = std::stoi(text, &used);
            if (used != text.size() || epoch < 1 || epoch > 1000) return 2;
            std::string line;
            while (std::getline(std::cin, line)) {
                if (line == "quit") { std::cout << "{\"control\":\"closed\"}" << std::endl; return 0; }
                size_t count_used = 0; int repeat = std::stoi(line, &count_used);
                if (count_used != line.size() || repeat < 1 || repeat > 1000) return 2;
                for (int i = 0; i < repeat; ++i) for (auto event : fixtures) {
                    event.epoch += epoch - 1;
                    if (mode == L"encoded") emit(decode(encode(event))); else emit(event);
                    if (!std::cout) return 4;
                }
                std::cout << "{\"control\":\"batch_end\",\"events\":" << repeat * 8
                    << ",\"wire_decodes\":" << (mode == L"encoded" ? repeat * 8 : 0) << "}" << std::endl;
            }
            return 0;
        }
        std::cerr << "Use identity | symbols IMAGE PDB | stack LOCAL_SYMBOL_DIRECTORY | events encoded|typed 1..1000\n";
        return 2;
    } catch (const std::exception&) { return 2; }
}
