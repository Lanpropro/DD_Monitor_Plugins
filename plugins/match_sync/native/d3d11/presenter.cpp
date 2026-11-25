#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <memory>
#include <stdexcept>
#include <string_view>

using Microsoft::WRL::ComPtr;
constexpr LONG pending_frame = 4;

// One producer and one consumer separate the writable, published and reading slots.
struct Header {
    volatile LONG middle;
    volatile LONG stop;
    UINT width, height, stride, slot_bytes;
    volatile LONG presented;
    std::byte reserved[36];
};
static_assert(sizeof(Header) == 64);

struct Frame {
    uint64_t sequence;
    int64_t timestamp_us;
};
static_assert(sizeof(Frame) == 16);

struct HandleCloser {
    void operator()(void* handle) const { CloseHandle(handle); }
};
struct ViewCloser {
    void operator()(void* view) const { UnmapViewOfFile(view); }
};
using Handle = std::unique_ptr<void, HandleCloser>;
using View = std::unique_ptr<void, ViewCloser>;

void check(HRESULT result, const char* stage) {
    if (FAILED(result)) {
        std::fprintf(stderr, "%s failed (0x%08lx)\n", stage, static_cast<unsigned long>(result));
        throw std::runtime_error(stage);
    }
}

void check_win(bool condition, const char* stage) {
    if (!condition) {
        const DWORD error = GetLastError();
        check(error ? HRESULT_FROM_WIN32(error) : E_FAIL, stage);
    }
}

LRESULT CALLBACK window_event(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_CLOSE || (message == WM_KEYDOWN && wparam == VK_ESCAPE)) {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

struct WindowCloser {
    void operator()(HWND window) const { DestroyWindow(window); }
};
using Window = std::unique_ptr<HWND__, WindowCloser>;

Window create_window(UINT width, UINT height, bool visible, HWND parent) {
    const auto instance = GetModuleHandleW(nullptr);
    const wchar_t class_name[] = L"DDM D3D11 isolated proof";
    WNDCLASSW description{};
    description.hInstance = instance;
    description.lpfnWndProc = window_event;
    description.lpszClassName = class_name;
    check_win(RegisterClassW(&description) != 0, "register presenter window");
    const DWORD style = parent ? WS_CHILD | WS_DISABLED :
        visible ? WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX : WS_POPUP;
    const DWORD extended = parent ? 0 : visible ? WS_EX_APPWINDOW : WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
    const int scale = visible ? 3 : 1;
    RECT bounds{0, 0, static_cast<LONG>(width) * scale, static_cast<LONG>(height) * scale};
    check_win(AdjustWindowRectEx(&bounds, style, FALSE, extended), "size presenter window");
    const int position = visible ? CW_USEDEFAULT : 0;
    Window window(CreateWindowExW(extended, class_name, L"DD Monitor CE - D3D11 60 Hz test", style,
        position, position, bounds.right - bounds.left, bounds.bottom - bounds.top,
        parent, nullptr, instance, nullptr));
    check_win(window != nullptr, "create presenter window");
    return window;
}

bool pump_messages() {
    MSG message{};
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
        if (message.message == WM_QUIT)
            return false;
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return true;
}

void verify_rows(ID3D11DeviceContext* context, ID3D11Texture2D* back_buffer,
                 ID3D11Texture2D* readback, const Header& header, const std::byte* pixels) {
    context->CopyResource(readback, back_buffer);
    D3D11_MAPPED_SUBRESOURCE mapped{};
    check(context->Map(readback, 0, D3D11_MAP_READ, 0, &mapped), "read back uploaded pixels");
    bool matches = true;
    for (UINT row = 0; row < header.height && matches; ++row) {
        const auto* actual = static_cast<const std::byte*>(mapped.pData) + row * mapped.RowPitch;
        matches = std::memcmp(actual, pixels + row * header.stride, header.width * 4) == 0;
    }
    context->Unmap(readback, 0);
    if (!matches)
        throw std::runtime_error("D3D11 readback differs from the producer pixels");
}

int run(const wchar_t* mapping_name, const wchar_t* event_name, bool warp, bool visible, HWND parent) {
    Handle mapping(OpenFileMappingW(FILE_MAP_WRITE, FALSE, mapping_name));
    check_win(mapping != nullptr, "open mailbox");
    View view(MapViewOfFile(mapping.get(), FILE_MAP_WRITE, 0, 0, 0));
    check_win(view != nullptr, "map mailbox");
    Handle frame_event(OpenEventW(SYNCHRONIZE, FALSE, event_name));
    check_win(frame_event != nullptr, "open frame event");
    auto& header = *static_cast<Header*>(view.get());
    if (!header.width || !header.height || header.width > 4096 || header.height > 4096
            || header.stride < header.width * 4 || header.stride % 8
            || uint64_t{header.stride} * header.height + sizeof(Frame) != header.slot_bytes)
        throw std::runtime_error("invalid mailbox geometry");
    MEMORY_BASIC_INFORMATION mapped{};
    if (!VirtualQuery(view.get(), &mapped, sizeof(mapped))
            || mapped.RegionSize < sizeof(Header) + 3ull * header.slot_bytes)
        throw std::runtime_error("mailbox shorter than three frames");
    const auto* slots = static_cast<const std::byte*>(view.get()) + sizeof(Header);

    const auto window = create_window(header.width, header.height, visible, parent);
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    D3D_FEATURE_LEVEL level{};
    check(D3D11CreateDevice(nullptr, warp ? D3D_DRIVER_TYPE_WARP : D3D_DRIVER_TYPE_HARDWARE,
        nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT | D3D11_CREATE_DEVICE_SINGLETHREADED, nullptr,
        0, D3D11_SDK_VERSION, &device, &level, &context), "create D3D11 device");
    ComPtr<IDXGIDevice> dxgi_device;
    check(device.As(&dxgi_device), "get DXGI device");
    ComPtr<IDXGIAdapter> adapter;
    check(dxgi_device->GetAdapter(&adapter), "get adapter");
    DXGI_ADAPTER_DESC adapter_desc{};
    check(adapter->GetDesc(&adapter_desc), "get adapter description");
    ComPtr<IDXGIFactory2> factory;
    check(adapter->GetParent(IID_PPV_ARGS(&factory)), "get DXGI factory");

    DXGI_SWAP_CHAIN_DESC1 swap_desc{};
    swap_desc.Width = header.width;
    swap_desc.Height = header.height;
    swap_desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    swap_desc.SampleDesc.Count = 1;
    swap_desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    swap_desc.BufferCount = 2;
    swap_desc.Scaling = DXGI_SCALING_STRETCH;
    swap_desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    ComPtr<IDXGISwapChain1> swap;
    check(factory->CreateSwapChainForHwnd(device.Get(), window.get(), &swap_desc, nullptr, nullptr,
        &swap), "create flip-model swap chain");
    ComPtr<ID3D11Texture2D> back_buffer;
    check(swap->GetBuffer(0, IID_PPV_ARGS(&back_buffer)), "get back buffer");
    D3D11_TEXTURE2D_DESC texture{};
    back_buffer->GetDesc(&texture);
    texture.Usage = D3D11_USAGE_STAGING;
    texture.BindFlags = 0;
    texture.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    texture.MiscFlags = 0;
    ComPtr<ID3D11Texture2D> readback;
    if (!visible && !parent)
        check(device->CreateTexture2D(&texture, nullptr, &readback), "create readback texture");

    if (parent) {
        std::printf("ready %llu\n", static_cast<unsigned long long>(reinterpret_cast<uintptr_t>(window.get())));
    } else if (visible) {
        ShowWindow(window.get(), SW_SHOWNORMAL);
        SetForegroundWindow(window.get());
        POINT origin{};
        RECT client{};
        check_win(ClientToScreen(window.get(), &origin) && GetClientRect(window.get(), &client),
            "measure visible window");
        std::printf("ready %ld %ld %ld %ld %u\n", origin.x, origin.y, client.right, client.bottom,
            static_cast<unsigned>(GetForegroundWindow() == window.get()));
    } else {
        std::puts("ready");
    }
    std::fflush(stdout);
    LONG front = 0;
    uint64_t consumed = 0, previous_sequence = 0, presents = 0, occluded = 0, other_success = 0;
    uint64_t paced = 0, backward_timestamps = 0;
    int64_t previous_timestamp = 0;
    const HANDLE event = frame_event.get();
    while (pump_messages()) {
        const LONG middle = InterlockedCompareExchange(&header.middle, 0, 0);
        if (!(middle & pending_frame)) {
            if (InterlockedCompareExchange(&header.stop, 0, 0)) {
                // The final publish may have raced the earlier read of middle.
                if (InterlockedCompareExchange(&header.middle, 0, 0) & pending_frame)
                    continue;
                break;
            }
            const DWORD waited = MsgWaitForMultipleObjects(1, &event, FALSE, 5000, QS_ALLINPUT);
            if (waited == WAIT_TIMEOUT && !parent)
                throw std::runtime_error("five seconds without producer activity");
            if (parent && !IsWindow(parent))
                break;
            check_win(waited != WAIT_FAILED, "wait for producer");
            continue;
        }
        front = InterlockedExchange(&header.middle, front) & 3;
        if (front > 2)
            throw std::runtime_error("invalid mailbox slot");
        const auto* slot = slots + front * header.slot_bytes;
        Frame frame{};
        std::memcpy(&frame, slot, sizeof(frame));
        if (frame.sequence <= previous_sequence)
            throw std::runtime_error("out-of-order frame submission");
        const auto* pixels = slot + sizeof(Frame);
        context->UpdateSubresource(back_buffer.Get(), 0, nullptr, pixels, header.stride, 0);
        if (readback)
            verify_rows(context.Get(), back_buffer.Get(), readback.Get(), header, pixels);
        if (!parent && (std::to_integer<uint8_t>(pixels[0]) != (frame.sequence & 0xff)
                || std::to_integer<uint8_t>(pixels[2]) != 0x62))
            throw std::runtime_error("shared slot pixel marker differs from the frame sequence");
        const HRESULT presented = swap->Present(1, 0);
        check(presented, "present");
        if (presented == S_OK)
            InterlockedIncrement(&header.presented);
        presents += presented == S_OK;
        occluded += presented == DXGI_STATUS_OCCLUDED;
        other_success += presented != S_OK && presented != DXGI_STATUS_OCCLUDED;
        ++consumed;
        paced += frame.sequence <= 60;
        backward_timestamps += previous_sequence != 0 && frame.timestamp_us < previous_timestamp;
        previous_sequence = frame.sequence;
        previous_timestamp = frame.timestamp_us;
    }
    std::printf("{\"backend\":\"%s\",\"vendor_id\":%u,\"device_id\":%u,\"feature_level\":%u,"
        "\"consumed\":%llu,\"verified_readbacks\":%llu,\"last_sequence\":%llu,"
        "\"last_timestamp_us\":%lld,\"present_s_ok\":%llu,\"present_occluded\":%llu,"
        "\"present_other_success\":%llu,\"paced_consumed\":%llu,\"backward_timestamps\":%llu}\n",
        warp ? "warp" : "hardware", adapter_desc.VendorId,
        adapter_desc.DeviceId, static_cast<UINT>(level), consumed, readback ? consumed : 0, previous_sequence,
        previous_timestamp, presents, occluded, other_success, paced, backward_timestamps);
    return 0;
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 3) {
        std::fputs("usage: presenter.exe MAP EVENT [--warp] [--visible | --parent HWND]\n", stderr);
        return 2;
    }
    bool warp = false, visible = false;
    HWND parent = nullptr;
    for (int index = 3; index < argc; ++index) {
        const std::wstring_view option(argv[index]);
        if (option == L"--warp")
            warp = true;
        else if (option == L"--visible")
            visible = true;
        else if (option == L"--parent" && index + 1 < argc) {
            wchar_t* end = nullptr;
            const auto id = std::wcstoull(argv[++index], &end, 10);
            if (!id || !end || *end)
                return 2;
            parent = reinterpret_cast<HWND>(static_cast<uintptr_t>(id));
        }
        else
            return 2;
    }
    try {
        if (visible && parent)
            return 2;
        if (visible || parent)
            SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        if (parent)
            check_win(IsWindow(parent), "validate parent window");
        return run(argv[1], argv[2], warp, visible, parent);
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
