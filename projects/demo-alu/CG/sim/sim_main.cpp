// sim_main.cpp - vòng mô phỏng cho Verilator --timing (thay main của --binary, vì bản đó không ghi coverage).
// Cố định, agent không sửa. +cov_file=<file> chọn nơi ghi coverage (mặc định coverage.dat).
#include <memory>
#include <string>
#include "verilated.h"
#include "verilated_cov.h"
#include "Vtb.h"

int main(int argc, char** argv) {
    const std::unique_ptr<VerilatedContext> ctx{new VerilatedContext};
    ctx->commandArgs(argc, argv);
    ctx->traceEverOn(true);   // testbench gọi $dumpfile khi có +vcd=
    const std::unique_ptr<Vtb> top{new Vtb{ctx.get(), ""}};
    while (!ctx->gotFinish()) {
        top->eval();
        if (!top->eventsPending()) break;
        ctx->time(top->nextTimeSlot());
    }
    top->final();
    const char* f = ctx->commandArgsPlusMatch("cov_file=");
    std::string path = (f && *f) ? std::string(f).substr(10) : std::string("coverage.dat");
    ctx->coveragep()->write(path.c_str());
    return 0;
}
